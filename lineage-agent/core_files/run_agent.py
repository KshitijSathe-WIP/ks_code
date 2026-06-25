# run_agent.py
# ────────────────────────────────────────────────────────────
# Lineage Agent — Interactive Chat with Function Calling
#
# Uses the OpenAI SDK directly with Azure AI Foundry API key.
# No Azure Identity / credential dance needed.
# ────────────────────────────────────────────────────────────

import os
import sys
import json
import inspect
import re
import threading
import time
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

# ─── Your lineage functions (the ACTUAL code that queries Neo4j) ───
# Imported lazily after connectivity checks since lineage_tools
# creates a Neo4j client at module level.
LINEAGE_TOOLS_MODULE = None

def _import_lineage_tools():
    """Import lineage_tools after connectivity is confirmed."""
    global LINEAGE_TOOLS_MODULE
    import lineage_tools
    LINEAGE_TOOLS_MODULE = lineage_tools

# Load .env from project root (parent of core_files/)
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# ────────────────────────────────────────────────────────────
# CONFIGURATION
# ────────────────────────────────────────────────────────────

PROJECT_ENDPOINT = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
API_KEY = os.environ["AZURE_AI_API_KEY"]
MODEL = os.environ.get("MODEL_DEPLOYMENT_NAME", "gpt-4.1-mini")

# ────────────────────────────────────────────────────────────
# FUNCTION REGISTRY (populated after preflight checks)
# ────────────────────────────────────────────────────────────

FUNCTION_REGISTRY = {}
TOOLS = []


def _init_tools():
    """Import lineage_tools and build function registry + tool schemas."""
    global FUNCTION_REGISTRY, TOOLS
    _import_lineage_tools()
    m = LINEAGE_TOOLS_MODULE
    FUNCTION_REGISTRY.update({
        "query_upstream_lineage":    m.query_upstream_lineage,
        "query_downstream_lineage":  m.query_downstream_lineage,
        "query_column_lineage":      m.query_column_lineage,
        "query_cross_layer_path":    m.query_cross_layer_path,
        "query_impact_analysis":     m.query_impact_analysis,
        "query_tables_by_layer":     m.query_tables_by_layer,
        "search_fields":             m.search_fields,
        "run_custom_cypher":         m.run_custom_cypher,
    })
    try:
        import cosmos_tools as ct
        FUNCTION_REGISTRY.update({
            "get_edge_transformation_details"    : ct.get_edge_transformation_details,
            "get_field_transformation_logic"     : ct.get_field_transformation_logic,
            "get_mapping_transformation_details" : ct.get_mapping_transformation_details,
            "get_lookup_details_for_table"       : ct.get_lookup_details_for_table,
            "get_sql_and_filter_logic"           : ct.get_sql_and_filter_logic,
            "get_edges_by_transformation_name"   : ct.get_edges_by_transformation_name,
        })
        print("   ✅ Cosmos DB tools loaded")
    except Exception as e:
        print(f"   ⚠️  Cosmos tools unavailable (continuing without them): {e}")
    TOOLS.extend([_build_tool_schema(fn) for fn in FUNCTION_REGISTRY.values()])

# ────────────────────────────────────────────────────────────
# BUILD TOOL SCHEMAS FROM FUNCTION SIGNATURES
# ────────────────────────────────────────────────────────────

def _extract_param_docs(docstring):
    params = {}
    if not docstring:
        return params
    for m in re.finditer(r':param\s+(\w+):\s+(.+?)(?=:param\s|:return|\Z)', docstring, re.DOTALL):
        params[m.group(1)] = re.sub(r'\s+', ' ', m.group(2)).strip()
    return params

def _to_json_type(ann):
    return {str: 'string', int: 'integer', float: 'number', bool: 'boolean'}.get(ann, 'string')

def _build_tool_schema(fn):
    sig = inspect.signature(fn)
    doc = inspect.getdoc(fn) or ''
    desc = doc.split('\n\n')[0].replace('\n', ' ').strip()
    param_docs = _extract_param_docs(doc)
    properties, required = {}, []
    for pname, param in sig.parameters.items():
        prop = {'type': _to_json_type(param.annotation)}
        if pname in param_docs:
            prop['description'] = param_docs[pname]
        properties[pname] = prop
        if param.default is inspect.Parameter.empty:
            required.append(pname)
    schema = {
        'type': 'function',
        'function': {
            'name': fn.__name__,
            'description': desc,
            'parameters': {
                'type': 'object',
                'properties': properties,
            }
        }
    }
    if required:
        schema['function']['parameters']['required'] = required
    return schema

# ────────────────────────────────────────────────────────────
# SYSTEM PROMPT
# ────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a Cross-Layer Data Lineage Assistant for a banking data warehouse.
You help users trace data flows across three layers:
- TPR (source/transactional systems)
- TT (staging/transformation layer)
- DDM (data mart/reporting layer)

The lineage graph has Field nodes connected by TRANSFORMS_TO relationships.
Field properties: id, db_schema, table_name, field_name, layer, data_type, precision.
Relationship properties: mapping_name, folder_name, transformation_name, transformation_type, expression.

CRITICAL EXECUTION RULE — NEVER narrate, ALWAYS act:
- NEVER say "I will now...", "Let me perform...", "I will run...", or "I will extract..." without ALSO issuing tool calls in the same response.
- Your FIRST response to any lineage question MUST contain tool_calls. Do NOT produce a text-only reply that describes your plan — call the tool immediately.
- If you need to explain your approach, do so AFTER the tool results are returned, not before.

FIELD-LEVEL IMPACT — when the user asks about impact of a specific FIELD (e.g. "what is impacted if TABLE.FIELD fails"):
  You MUST call BOTH tools in the same response:
  1. query_impact_analysis(table_name=<bare table name>) — for the blast radius by layer
  2. query_column_lineage(field_name=<field>, table_name=<table>) — for the field-level downstream transformation chain
  Present BOTH results: a summary table of impacted tables AND the field-level flow with transformation details.

When answering:
1. Use the provided tools to query the Neo4j lineage graph — ALWAYS call tools, never answer from memory
2. Present results clearly with table names, layers, and field counts
3. For impact analysis, summarize the blast radius by layer
4. For column lineage, show the transformation chain with expressions
5. If a table/field is not found, call search_fields before saying "not found"
"""

# ────────────────────────────────────────────────────────────
# OPENAI CLIENT
# ────────────────────────────────────────────────────────────

client = OpenAI(
    base_url=f"{PROJECT_ENDPOINT}/openai/v1",
    api_key=API_KEY,
)

# ────────────────────────────────────────────────────────────
# CONNECTIVITY CHECKS
# ────────────────────────────────────────────────────────────

def check_neo4j_connectivity():
    """Verify Neo4j is reachable. Returns True on success, False on failure."""
    try:
        from neo4j import GraphDatabase
        from neo4j_client import Neo4jLineageClient
        uri = os.environ.get("NEO4J_URI", "")
        username = os.environ.get("NEO4J_USERNAME", "")
        password = os.environ.get("NEO4J_PASSWORD", "")
        if not all([uri, username, password]):
            print("\n❌ Missing Neo4j environment variables (NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD)")
            return False
        uri_for_driver = uri.replace("neo4j+s://", "neo4j+ssc://", 1)
        driver = GraphDatabase.driver(uri_for_driver, auth=(username, password))
        driver.verify_connectivity()
        driver.close()
        return True
    except Exception as e:
        print(f"\n❌ Neo4j connectivity check failed: {e}")
        return False


def check_model_connectivity():
    """Verify the GPT model endpoint is reachable. Returns True on success, False on failure."""
    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": "ping"}],
            max_completion_tokens=5,
        )
        if response.choices and response.choices[0].message.content:
            return True
        return False
    except Exception as e:
        print(f"\n❌ Model connectivity check failed: {e}")
        return False


def preflight_checks():
    """Run all connectivity checks. Exits if any fail."""
    print("\n🔍 Running preflight checks...")

    neo4j_ok = check_neo4j_connectivity()
    model_ok = check_model_connectivity()

    if neo4j_ok:
        print("   ✅ Neo4j — connected")
    else:
        print("   ❌ Neo4j — unreachable")

    if model_ok:
        print("   ✅ GPT Model — connected")
    else:
        print("   ❌ GPT Model — unreachable")

    if not (neo4j_ok and model_ok):
        print("\n⛔ Agent cannot start — fix the above issues and try again.")
        sys.exit(1)

    print("   ✅ All checks passed")

    # Now safe to import lineage_tools (which connects to Neo4j at module level)
    _init_tools()
    print("   ✅ Tools loaded\n")


# ────────────────────────────────────────────────────────────
# CONVERSATION LOOP WITH TOOL CALLING
# ────────────────────────────────────────────────────────────

def _thinking_ticker(stop_event, label="Thinking"):
    """
    Background thread: prints an elapsed-time counter to stderr every second.
    Clears itself when stop_event is set.
    """
    start = time.time()
    while not stop_event.is_set():
        elapsed = int(time.time() - start)
        # \r returns to start of line; the spaces overwrite previous content
        sys.stderr.write(f"\r⏳ {label}… {elapsed}s   ")
        sys.stderr.flush()
        stop_event.wait(1.0)
    # Clear the ticker line when done
    sys.stderr.write("\r" + " " * 30 + "\r")
    sys.stderr.flush()


def chat(messages):
    """
    Send messages to the model with streaming. Tokens are printed to the
    terminal as they arrive. If the model requests tool calls, execute them
    and continue until a final text response is produced.
    Returns the full assembled response text.
    """
    max_rounds = 10
    for round_num in range(max_rounds):
        # Show live elapsed-time ticker while waiting for the model
        stop_ticker = threading.Event()
        ticker = threading.Thread(
            target=_thinking_ticker,
            args=(stop_ticker, "Model thinking"),
            daemon=True,
        )
        ticker.start()

        stream = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
            stream=True,
            max_completion_tokens=8000,
        )

        # Accumulate streamed response
        full_text = ""
        tool_calls_acc = {}   # index -> {id, name, arguments}
        finish_reason = None
        first_chunk = True

        for chunk in stream:
            if first_chunk:
                # First chunk received — stop the ticker
                stop_ticker.set()
                ticker.join()
                first_chunk = False
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            finish_reason = chunk.choices[0].finish_reason or finish_reason
            # Stream text tokens directly to terminal
            if delta.content:
                print(delta.content, end="", flush=True)
                full_text += delta.content

            # Accumulate tool call deltas
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in tool_calls_acc:
                        tool_calls_acc[idx] = {"id": tc.id or "", "name": "", "arguments": ""}
                    if tc.id:
                        tool_calls_acc[idx]["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            tool_calls_acc[idx]["name"] += tc.function.name
                        if tc.function.arguments:
                            tool_calls_acc[idx]["arguments"] += tc.function.arguments

        # If no tool calls, the streamed text is the final answer
        if not tool_calls_acc:
            print()  # newline after streamed output
            messages.append({"role": "assistant", "content": full_text or "(No response generated)"})
            return full_text or "(No response generated)"

        # Build assistant message with tool_calls for conversation history
        tool_calls_list = []
        for idx in sorted(tool_calls_acc):
            tc = tool_calls_acc[idx]
            tool_calls_list.append({
                "id": tc["id"],
                "type": "function",
                "function": {"name": tc["name"], "arguments": tc["arguments"]},
            })
        messages.append({
            "role": "assistant",
            "content": full_text or None,
            "tool_calls": tool_calls_list,
        })

        # Execute tool calls
        print(f"\n{'─'*50}")
        print(f"🔧 Agent requesting {len(tool_calls_list)} tool call(s):")

        for tc in tool_calls_list:
            func_name = tc["function"]["name"]
            try:
                func_args = json.loads(tc["function"]["arguments"])
            except json.JSONDecodeError:
                func_args = {}

            print(f"\n   📌 Function: {func_name}")
            print(f"   📌 Args:     {json.dumps(func_args, indent=2)}")

            if func_name in FUNCTION_REGISTRY:
                try:
                    result = FUNCTION_REGISTRY[func_name](**func_args)
                    print(f"   ✅ Success — returned {len(result)} chars")
                except Exception as e:
                    result = json.dumps({"error": str(e), "function": func_name})
                    print(f"   ❌ Error: {e}")
            else:
                result = json.dumps({"error": f"Unknown function: {func_name}"})
                print(f"   ⚠️ Unknown function: {func_name}")

            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result,
            })

        print(f"{'─'*50}\n")
        print("\n🤖 Agent:\n", end="", flush=True)  # print header before next stream

    return "(Max rounds reached)"


# ────────────────────────────────────────────────────────────
# INTERACTIVE MAIN
# ────────────────────────────────────────────────────────────

def main():
    print("\n" + "=" * 60)
    print("🧬 CROSS-LAYER LINEAGE ASSISTANT")
    print("=" * 60)
    print(f"Model: {MODEL}")
    print(f"Endpoint: {PROJECT_ENDPOINT}")
    print("─" * 60)

    # Verify Neo4j and GPT model are reachable before starting
    preflight_checks()

    print("Ask questions about data lineage.")
    print("Type 'quit' or 'exit' to stop.")
    print("Type 'new' to start a new conversation.")
    print("=" * 60)

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    while True:
        print()
        user_input = input("You: ").strip()

        if not user_input:
            continue

        if user_input.lower() in ["quit", "exit", "q"]:
            print("\n👋 Goodbye!")
            break

        if user_input.lower() == "new":
            messages = [{"role": "system", "content": SYSTEM_PROMPT}]
            print("\n📎 New conversation started.")
            continue

        messages.append({"role": "user", "content": user_input})

        try:
            print("\n🤖 Agent:\n", end="", flush=True)
            chat(messages)
        except Exception as e:
            print(f"\n❌ Error: {e}")
            # Remove failed user message so conversation stays consistent
            messages.pop()


if __name__ == "__main__":
    main()
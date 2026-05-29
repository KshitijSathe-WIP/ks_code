# web_app.py
# ────────────────────────────────────────────────────────────
# Lineage Agent — Web Frontend (Flask)
#
# Provides a web-based chat interface to the same lineage agent.
# The CLI (core_files/run_agent.py) remains unchanged.
# ────────────────────────────────────────────────────────────

import os
import sys
import queue
import json
import uuid
import inspect
import re
import threading
import time
from pathlib import Path
from dotenv import load_dotenv
from flask import Flask, request, jsonify, send_from_directory, Response
from openai import OpenAI

# Add core_files to path so we can import lineage_tools
sys.path.insert(0, str(Path(__file__).resolve().parent / "core_files"))

# Load .env from this directory
load_dotenv(Path(__file__).resolve().parent / ".env")

# ────────────────────────────────────────────────────────────
# CONFIGURATION
# ────────────────────────────────────────────────────────────

PROJECT_ENDPOINT = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
API_KEY = os.environ["AZURE_AI_API_KEY"]
MODEL = os.environ.get("MODEL_DEPLOYMENT_NAME", "gpt-4.1-mini")

# ────────────────────────────────────────────────────────────
# FLASK APP
# ────────────────────────────────────────────────────────────

app = Flask(__name__, static_folder="static")

# ────────────────────────────────────────────────────────────
# OPENAI CLIENT
# ────────────────────────────────────────────────────────────

client = OpenAI(
    base_url=f"{PROJECT_ENDPOINT}/openai/v1",
    api_key=API_KEY,
    timeout=90.0,  # 90 second timeout per API call
)

# ────────────────────────────────────────────────────────────
# FUNCTION REGISTRY & TOOL SCHEMAS
# ────────────────────────────────────────────────────────────

FUNCTION_REGISTRY = {}
TOOLS = []
TOOLS_LOADED = False


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


def init_tools():
    """Import lineage_tools and build function registry + tool schemas."""
    global FUNCTION_REGISTRY, TOOLS, TOOLS_LOADED
    if TOOLS_LOADED:
        return True
    try:
        import lineage_tools as m
        FUNCTION_REGISTRY.update({
            "query_upstream_lineage":    m.query_upstream_lineage,
            "query_downstream_lineage":  m.query_downstream_lineage,
            "query_column_lineage":      m.query_column_lineage,
            "query_cross_layer_path":    m.query_cross_layer_path,
            "query_impact_analysis":     m.query_impact_analysis,
            "query_tables_by_layer":     m.query_tables_by_layer,
            "run_custom_cypher":         m.run_custom_cypher,
        })
        TOOLS.extend([_build_tool_schema(fn) for fn in FUNCTION_REGISTRY.values()])
        TOOLS_LOADED = True
        return True
    except Exception as e:
        print(f"❌ Failed to load tools: {e}")
        return False


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

When answering:
1. Use the provided tools to query the Neo4j lineage graph
2. Present tabular results using **Markdown tables** (with headers and alignment)
3. For lineage paths and data flows, render a **Mermaid flowchart** using ```mermaid code blocks
4. For impact analysis, show a Mermaid diagram of the blast radius plus a summary table
5. For column lineage, show both a Mermaid transformation chain AND a table with expressions
6. If a table/field is not found, suggest similar names or ask the user to clarify

Formatting rules:
- Always use Markdown tables (| col1 | col2 |) when showing lists of tables, fields, or properties
- Always complete all table rows — never truncate or abbreviate
- Use Mermaid graph TD (top-down) for lineage flows, e.g.:
  ```mermaid
  graph TD
    A["TPR: SOURCE_TABLE.FIELD"] -->|expression| B["TT: STAGING_TABLE.FIELD"]
    B -->|expression| C["DDM: MART_TABLE.FIELD"]
  ```
- IMPORTANT Mermaid rules:
  - Always wrap node labels in double quotes: A["label"]
  - Use colons not dots for layer separation: "TPR: TABLE.FIELD"
  - Do NOT use parentheses () or brackets [] inside quoted labels
  - Keep edge labels short, no special characters in edge labels
  - Use simple single-letter or short node IDs: A, B, C, n1, n2
- Bold important field names and table names in text explanations
"""

# ────────────────────────────────────────────────────────────
# SESSION MANAGEMENT (in-memory)
# ────────────────────────────────────────────────────────────

sessions = {}


def get_or_create_session(session_id=None):
    """Get existing session or create a new one."""
    if session_id and session_id in sessions:
        return session_id, sessions[session_id]
    new_id = str(uuid.uuid4())
    sessions[new_id] = [{"role": "system", "content": SYSTEM_PROMPT}]
    return new_id, sessions[new_id]


# ────────────────────────────────────────────────────────────
# CHAT LOGIC (same as CLI)
# ────────────────────────────────────────────────────────────

def chat(messages):
    """
    Send messages to the model. If the model requests tool calls,
    execute them and continue until a final text response is produced.
    Returns (response_text, tool_calls_log).
    """
    tool_calls_log = []
    max_rounds = 10  # prevent infinite loops

    for _ in range(max_rounds):
        try:
            kwargs = {
                "model": MODEL,
                "messages": messages,
                "max_completion_tokens": 8000,
            }
            if TOOLS:  # only pass tools if they were loaded
                kwargs["tools"] = TOOLS
                kwargs["tool_choice"] = "auto"

            print(f"   [chat] Calling model (messages: {len(messages)})...")
            response = client.chat.completions.create(**kwargs)
        except Exception as e:
            print(f"   [chat] ❌ API error: {e}")
            raise

        choice = response.choices[0]
        msg = choice.message

        # Serialize assistant message for conversation history
        msg_dict = msg.model_dump()
        # Remove None tool_calls to avoid API issues on next round
        if msg_dict.get("tool_calls") is None:
            msg_dict.pop("tool_calls", None)
        messages.append(msg_dict)

        # If no tool calls, we have the final answer
        if not msg.tool_calls:
            content = msg.content or "(No response generated)"
            print(f"   [chat] ✅ Final response ({len(content)} chars)")
            return content, tool_calls_log

        print(f"   [chat] 🔧 {len(msg.tool_calls)} tool call(s) requested")

        for tool_call in msg.tool_calls:
            func_name = tool_call.function.name
            try:
                func_args = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError as e:
                func_args = {}
                result = json.dumps({"error": f"Invalid arguments: {e}"})
                tool_calls_log.append({"function": func_name, "args": {}, "status": "error", "error": str(e)})
                messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": result})
                continue

            log_entry = {"function": func_name, "args": func_args}

            if func_name in FUNCTION_REGISTRY:
                try:
                    print(f"   [chat]    → {func_name}({func_args})")
                    result = FUNCTION_REGISTRY[func_name](**func_args)
                    if result is None:
                        result = json.dumps({"result": "No data returned"})
                    log_entry["status"] = "success"
                    log_entry["result_length"] = len(result)
                    print(f"   [chat]    ✅ {func_name} returned {len(result)} chars")
                except Exception as e:
                    result = json.dumps({"error": str(e), "function": func_name})
                    log_entry["status"] = "error"
                    log_entry["error"] = str(e)
                    print(f"   [chat]    ❌ {func_name} error: {e}")
            else:
                result = json.dumps({"error": f"Unknown function: {func_name}"})
                log_entry["status"] = "error"
                log_entry["error"] = f"Unknown function: {func_name}"
                print(f"   [chat]    ⚠️ Unknown function: {func_name}")

            tool_calls_log.append(log_entry)

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result,
            })

    # If we exhausted max_rounds
    return "I was unable to complete the analysis within the allowed number of steps. Please try a simpler query.", tool_calls_log


# ────────────────────────────────────────────────────────────
# ROUTES
# ────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.get_json()
    if not data or "message" not in data:
        return jsonify({"error": "Missing 'message' field"}), 400

    user_message = data["message"].strip()
    if not user_message:
        return jsonify({"error": "Empty message"}), 400

    session_id = data.get("session_id")
    session_id, messages = get_or_create_session(session_id)

    messages.append({"role": "user", "content": user_message})

    try:
        print(f"\n📨 User: {user_message[:80]}...")
        response_text, tool_calls_log = chat(messages)
        print(f"📤 Response sent ({len(response_text)} chars)\n")
        return jsonify({
            "session_id": session_id,
            "response": response_text,
            "tool_calls": tool_calls_log,
        })
    except Exception as e:
        # Remove failed user message
        messages.pop()
        error_msg = f"{type(e).__name__}: {str(e)}"
        print(f"❌ Chat error: {error_msg}\n")
        return jsonify({"error": error_msg}), 500


@app.route("/api/new_session", methods=["POST"])
def new_session():
    session_id, _ = get_or_create_session()
    return jsonify({"session_id": session_id})


# ────────────────────────────────────────────────────────────
# STREAMING CHAT (SSE)
# Background thread does all model work; generator yields keepalives every
# second so Flask flushes HTTP chunks regardless of how slow the model is.
# ────────────────────────────────────────────────────────────

_DONE = object()  # sentinel


def _execute_tool_call(func_name, func_args):
    """Execute a single tool call. Returns (result_str, log_entry)."""
    log_entry = {"function": func_name, "args": func_args}
    if func_name in FUNCTION_REGISTRY:
        try:
            print(f"   [stream]    → {func_name}({func_args})")
            result = FUNCTION_REGISTRY[func_name](**func_args)
            if result is None:
                result = json.dumps({"result": "No data returned"})
            log_entry["status"] = "success"
            log_entry["result_length"] = len(result)
            print(f"   [stream]    ✅ {func_name} returned {len(result)} chars")
        except Exception as e:
            result = json.dumps({"error": str(e), "function": func_name})
            log_entry["status"] = "error"
            log_entry["error"] = str(e)
            print(f"   [stream]    ❌ {func_name} error: {e}")
    else:
        result = json.dumps({"error": f"Unknown function: {func_name}"})
        log_entry["status"] = "error"
        log_entry["error"] = f"Unknown function: {func_name}"
    return result, log_entry


def _agent_worker(messages, q):
    """
    Background thread: runs the full model+tool loop and puts SSE event
    dicts onto q. Puts _DONE sentinel when finished.
    """
    try:
        all_tool_calls_log = []
        max_rounds = 10

        for round_num in range(max_rounds):
            kwargs = {
                "model": MODEL,
                "messages": messages,
                "max_completion_tokens": 8000,
                "stream": True,
            }
            if TOOLS:
                kwargs["tools"] = TOOLS
                kwargs["tool_choice"] = "auto"

            print(f"   [stream] Round {round_num + 1}: calling model (messages: {len(messages)})...")
            stream = client.chat.completions.create(**kwargs)

            content_parts = []
            tool_calls_by_index = {}
            is_tool_call = False

            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta

                if delta.tool_calls:
                    is_tool_call = True
                    for tc_delta in delta.tool_calls:
                        idx = tc_delta.index
                        if idx not in tool_calls_by_index:
                            tool_calls_by_index[idx] = {"id": "", "function_name": "", "arguments": ""}
                        if tc_delta.id:
                            tool_calls_by_index[idx]["id"] = tc_delta.id
                        if tc_delta.function:
                            if tc_delta.function.name:
                                tool_calls_by_index[idx]["function_name"] += tc_delta.function.name
                            if tc_delta.function.arguments:
                                tool_calls_by_index[idx]["arguments"] += tc_delta.function.arguments

                if delta.content:
                    content_parts.append(delta.content)
                    if not is_tool_call:
                        q.put({"type": "token", "token": delta.content})

            content = "".join(content_parts)

            if is_tool_call:
                tool_calls = [tool_calls_by_index[i] for i in sorted(tool_calls_by_index)]
                print(f"   [stream] 🔧 {len(tool_calls)} tool call(s) requested")

                messages.append({
                    "role": "assistant",
                    "content": content or None,
                    "tool_calls": [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {"name": tc["function_name"], "arguments": tc["arguments"]},
                        }
                        for tc in tool_calls
                    ],
                })

                for tc in tool_calls:
                    func_name = tc["function_name"]
                    try:
                        func_args = json.loads(tc["arguments"])
                    except json.JSONDecodeError as e:
                        func_args = {}
                        result = json.dumps({"error": f"Invalid arguments: {e}"})
                        all_tool_calls_log.append({"function": func_name, "args": {}, "status": "error", "error": str(e)})
                        messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
                        continue

                    result, log_entry = _execute_tool_call(func_name, func_args)
                    all_tool_calls_log.append(log_entry)
                    messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})

                q.put({"type": "tools", "tool_calls": all_tool_calls_log})

            else:
                messages.append({"role": "assistant", "content": content or "(No response generated)"})
                if not content:
                    q.put({"type": "token", "token": "(No response generated)"})
                print(f"   [stream] ✅ Done ({len(content)} chars)")
                q.put({"type": "done"})
                return

        q.put({"type": "token", "token": "Unable to complete analysis within the allowed number of steps."})
        q.put({"type": "done"})

    except Exception as e:
        error_msg = f"{type(e).__name__}: {str(e)}"
        print(f"   [stream] ❌ Error: {error_msg}")
        q.put({"type": "error", "error": error_msg})
    finally:
        q.put(_DONE)


@app.route("/api/chat/stream", methods=["POST"])
def api_chat_stream():
    data = request.get_json()
    if not data or "message" not in data:
        return jsonify({"error": "Missing 'message' field"}), 400

    user_message = data["message"].strip()
    if not user_message:
        return jsonify({"error": "Empty message"}), 400

    session_id = data.get("session_id")
    session_id, messages = get_or_create_session(session_id)
    messages.append({"role": "user", "content": user_message})

    def generate():
        print(f"\n📨 [stream] User: {user_message[:80]}...")

        # Send session id immediately
        yield f"data: {json.dumps({'type': 'session', 'session_id': session_id})}\n\n"

        q = queue.Queue()

        # Start agent work in background thread
        t = threading.Thread(target=_agent_worker, args=(messages, q), daemon=True)
        t.start()

        # Yield events as they arrive; send SSE comment keepalives while waiting
        # so Flask flushes HTTP chunks and the browser sees activity immediately
        while True:
            try:
                item = q.get(timeout=1.0)
            except queue.Empty:
                # Keepalive comment — forces Werkzeug to flush this HTTP chunk
                yield ": keepalive\n\n"
                continue

            if item is _DONE:
                break

            yield f"data: {json.dumps(item)}\n\n"

            if item.get("type") in ("done", "error"):
                break

        # Clean up on error: remove dangling user message
        if messages and messages[-1].get("role") == "user":
            messages.pop()

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "model": MODEL,
        "tools_loaded": TOOLS_LOADED,
        "active_sessions": len(sessions),
    })


# ────────────────────────────────────────────────────────────
# HEARTBEAT / AUTO-SHUTDOWN
# ────────────────────────────────────────────────────────────

HEARTBEAT_TIMEOUT = 15  # seconds without heartbeat before shutdown
_last_heartbeat = time.time()
_heartbeat_lock = threading.Lock()


@app.route("/api/heartbeat", methods=["POST"])
def heartbeat():
    """Browser pings this every few seconds to keep server alive."""
    global _last_heartbeat
    with _heartbeat_lock:
        _last_heartbeat = time.time()
    return "", 204


@app.route("/api/shutdown", methods=["POST"])
def shutdown():
    """Immediate shutdown triggered by browser beforeunload."""
    print("\n👋 Browser disconnected — shutting down.")
    _shutdown_server()
    return "", 204


def _shutdown_server():
    """Terminate the Flask process."""
    os._exit(0)


def _heartbeat_watchdog():
    """Background thread that shuts down server if no heartbeat received."""
    while True:
        time.sleep(5)
        with _heartbeat_lock:
            elapsed = time.time() - _last_heartbeat
        if elapsed > HEARTBEAT_TIMEOUT:
            print(f"\n👋 No heartbeat for {int(elapsed)}s — shutting down.")
            _shutdown_server()


# ────────────────────────────────────────────────────────────
# MAIN
# ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("🧬 LINEAGE AGENT — Web Interface")
    print("=" * 60)
    print(f"Model: {MODEL}")
    print(f"Endpoint: {PROJECT_ENDPOINT}")
    print("─" * 60)

    print("\n🔍 Initializing tools...")
    if init_tools():
        print("   ✅ Tools loaded")
    else:
        print("   ❌ Tools failed to load — agent will not have lineage functions")

    # Start heartbeat watchdog thread
    watchdog = threading.Thread(target=_heartbeat_watchdog, daemon=True)
    watchdog.start()

    print("\n🌐 Starting web server...")
    print("   Open http://localhost:5000 in your browser")
    print("   Server will auto-shutdown when browser is closed.")
    print("=" * 60 + "\n")

    app.run(host="0.0.0.0", port=5000, debug=False)

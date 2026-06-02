"""
extract_transformation_details.py
----------------------------------
Parses the same Informatica PowerCenter XML export as extract_lineage.py and
produces a second JSON file with detailed transformation logic for every edge.

Output format — flat array of records, one record per edge:
  {
    "edge_id"                  : "SRC.TABLE.FIELD__to__TGT.TABLE.FIELD__m_MAPPING",
    "from_vertex"              : "SRC.TABLE.FIELD",
    "to_vertex"                : "TGT.TABLE.FIELD",
    "mapping_name"             : "m_...",
    "folder_name"              : "2_CRDM_DIS",
    "final_expression"         : "<expression closest to target>",
    "transformation_chain"     : [          ← ordered list of intermediate steps
      {
        "step"                      : 1,
        "transformation_name"       : "sq_SOURCE",
        "transformation_type"       : "Source Qualifier",
        "input_port"                : "FIELD",
        "output_port"               : "FIELD",
        "port_expression"           : "",
        "custom_sql"                : "SELECT ...",
        "lookup_condition"          : "",
        "lookup_table_name"         : "",
        "filter_condition"          : "",
        "update_strategy_expression": "",
        "join_condition"            : "",
        "all_ports"                 : { "<port>": "<expression>", ... }
      },
      ...
    ]
  }

This structure maps directly to two relational tables:
  edge_transformation_details  (one row per edge_id)
  edge_transformation_steps    (one row per step, FK = edge_id)

Usage:
  python extract_transformation_details.py \\
      --xml    "Input XML/wf_TPR_to_DDM_SHAW_sample.XML" \\
      --output "Output Files/transformation_details.json"

  # Optionally supply an existing lineage JSON to cross-reference edge IDs:
  python extract_transformation_details.py \\
      --xml       "Input XML/wf_TPR_to_DDM_SHAW_sample.XML" \\
      --lineage   "Output Files/sample_lineage.json" \\
      --output    "Output Files/transformation_details.json"
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from xml.etree import ElementTree as ET


# ---------------------------------------------------------------------------
# Constants — kept in sync with extract_lineage.py
# ---------------------------------------------------------------------------

TT_PREFIXES = ("TT_", "WRK_")
DDM_SCHEMAS = ("CRDM_DDM", "NCNO_DDM", "DDM", "ADDX_DDM")
GENERIC_SCHEMAS = {"ORACLE", "FLAT FILE", "UNKNOWN", ""}
DEFAULT_DDM_SCHEMA = "CRDM_DDM"
DEFAULT_TT_SCHEMA = "CRDM_TMP"

# TABLE_ATTRIBUTE names to capture per transformation type
_SQ_ATTRS  = {"User Defined Join", "Filter Condition", "Source Filter",
               "User Defined Query", "Pre SQL", "Post SQL", "Number of Sorted Ports"}
_LKP_ATTRS = {"Lookup condition", "Lookup Condition", "Lookup table name",
               "Lookup Table Name", "Lookup cache persistent",
               "Lookup policy on multiple match"}
_UPD_ATTRS = {"Update strategy expression", "Update Strategy Expression",
               "Forward Rejected Rows"}
_FLT_ATTRS = {"Filter Condition", "Filter condition"}
_AGG_ATTRS = {"Sorted Input", "Cache Directory", "Cache Size"}
_ROU_ATTRS = {}  # Router group conditions live as TRANSFORMFIELD with PORTTYPE=CONDITION
_ALL_ATTRS  = _SQ_ATTRS | _LKP_ATTRS | _UPD_ATTRS | _FLT_ATTRS | _AGG_ATTRS


# ---------------------------------------------------------------------------
# Helpers — identical to extract_lineage.py
# ---------------------------------------------------------------------------

def normalise_schema(db_schema: str, table_name: str, layer: str) -> str:
    s = (db_schema or "").upper()
    if s in GENERIC_SCHEMAS:
        if layer == "TT":
            return DEFAULT_TT_SCHEMA
        if layer == "DDM":
            return DEFAULT_DDM_SCHEMA
    return s


def classify_layer(table_name: str, db_schema: str | None, element_type: str) -> str:
    t = (table_name or "").upper()
    s = (db_schema or "").upper()
    if t.startswith(TT_PREFIXES):
        return "TT"
    if s in DDM_SCHEMAS or any(ddm in s for ddm in DDM_SCHEMAS):
        return "DDM"
    if element_type == "target" and not t.startswith(TT_PREFIXES):
        return "DDM"
    if element_type == "source":
        return "TPR"
    return "UNKNOWN"


def make_field_id(db_schema: str, table_name: str, field_name: str) -> str:
    return f"{db_schema.upper()}.{table_name.upper()}.{field_name.upper()}"


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def parse_sources(folder: ET.Element) -> dict:
    sources = {}
    for src in folder.findall("SOURCE"):
        name = src.get("NAME", "")
        db_schema = src.get("DBDNAME") or src.get("OWNERNAME") or "UNKNOWN"
        layer = classify_layer(name, db_schema, "source")
        db_schema = normalise_schema(db_schema, name, layer)
        fields = {}
        for f in src.findall("SOURCEFIELD"):
            fname = f.get("NAME", "")
            fields[fname] = {"datatype": f.get("DATATYPE", ""), "precision": f.get("PRECISION", "")}
        sources[name] = {"db_schema": db_schema, "table_name": name, "fields": fields}
    return sources


def parse_targets(folder: ET.Element) -> dict:
    targets = {}
    for tgt in folder.findall("TARGET"):
        name = tgt.get("NAME", "")
        db_schema = tgt.get("OWNERNAME") or tgt.get("DATABASETYPE") or "UNKNOWN"
        layer = classify_layer(name, db_schema, "target")
        db_schema = normalise_schema(db_schema, name, layer)
        fields = {}
        for f in tgt.findall("TARGETFIELD"):
            fname = f.get("NAME", "")
            fields[fname] = {"datatype": f.get("DATATYPE", ""), "precision": f.get("PRECISION", "")}
        targets[name] = {"db_schema": db_schema, "table_name": name, "fields": fields}
    return targets


def parse_shortcuts(folder: ET.Element) -> dict:
    shortcuts = {}
    for sc in folder.findall("SHORTCUT"):
        name = sc.get("NAME", "")
        shortcuts[name] = {
            "ref_object_name": sc.get("REFOBJECTNAME", ""),
            "folder_name": sc.get("FOLDERNAME", ""),
            "object_type": sc.get("OBJECTTYPE", ""),
        }
    return shortcuts


def parse_reusable_transformations(folder: ET.Element) -> dict:
    return _parse_transformations(folder.findall("TRANSFORMATION"))


def _parse_transformations(t_elements) -> dict:
    """
    Parse a list of TRANSFORMATION XML elements into a rich dict:
      { name: {
            type,
            fields: { name: {expression, datatype, precision, porttype} },
            attributes: { attr_name: value },   ← TABLE_ATTRIBUTE values
        }
      }
    """
    transforms = {}
    for t in t_elements:
        name = t.get("NAME", "")
        t_type = t.get("TYPE", "")

        fields = {}
        for tf in t.findall("TRANSFORMFIELD"):
            fname = tf.get("NAME", "")
            fields[fname] = {
                "expression"  : tf.get("EXPRESSION") or tf.get("DEFAULTVALUE") or "",
                "datatype"    : tf.get("DATATYPE", ""),
                "precision"   : tf.get("PRECISION", ""),
                "porttype"    : tf.get("PORTTYPE", ""),
            }

        # Capture all TABLEATTRIBUTE values (lookup cond, SQ SQL, filter, etc.)
        attributes = {}
        for ta in t.findall("TABLEATTRIBUTE"):
            attr_name = ta.get("NAME", "")
            if attr_name:
                attributes[attr_name] = ta.get("VALUE", "")

        transforms[name] = {"type": t_type, "fields": fields, "attributes": attributes}
    return transforms


def _build_transform_step(t_name: str, t_type: str, input_port: str,
                           output_port: str, t_info: dict, step: int) -> dict:
    """
    Build one step dict for the transformation_chain array.
    Extracts all known attributes into named fields for easy SQL querying.
    """
    attrs = t_info.get("attributes", {})
    fields = t_info.get("fields", {})

    # Port-level expression (prefer output port, fall back to input)
    port_expr = ""
    if output_port and output_port in fields:
        port_expr = fields[output_port].get("expression", "")
    if not port_expr and input_port and input_port in fields:
        port_expr = fields[input_port].get("expression", "")

    # All port expressions — useful for understanding the full transformation
    all_ports = {pname: pinfo.get("expression", "") for pname, pinfo in fields.items()}

    # Named extraction of well-known attributes
    custom_sql = (
        attrs.get("User Defined Query")
        or attrs.get("Pre SQL")
        or ""
    )
    lookup_condition = (
        attrs.get("Lookup condition")
        or attrs.get("Lookup Condition")
        or ""
    )
    lookup_table_name = (
        attrs.get("Lookup table name")
        or attrs.get("Lookup Table Name")
        or ""
    )
    filter_condition = (
        attrs.get("Filter Condition")
        or attrs.get("Filter condition")
        or attrs.get("Source Filter")
        or ""
    )
    update_strategy_expression = (
        attrs.get("Update strategy expression")
        or attrs.get("Update Strategy Expression")
        or ""
    )
    join_condition = attrs.get("User Defined Join", "")

    return {
        "step"                       : step,
        "transformation_name"        : t_name,
        "transformation_type"        : t_type,
        "input_port"                 : input_port,
        "output_port"                : output_port,
        "port_expression"            : port_expr,
        "custom_sql"                 : custom_sql,
        "lookup_condition"           : lookup_condition,
        "lookup_table_name"          : lookup_table_name,
        "filter_condition"           : filter_condition,
        "update_strategy_expression" : update_strategy_expression,
        "join_condition"             : join_condition,
        "all_ports"                  : all_ports,
        "raw_attributes"             : attrs,     # keep everything for future use
    }


# ---------------------------------------------------------------------------
# Mapping-level detail extraction
# ---------------------------------------------------------------------------

PASSTHROUGH_TYPES = {"Lookup Procedure", "Stored Procedure"}


def extract_mapping_details(
    mapping: ET.Element,
    folder_name: str,
    sources: dict,
    targets: dict,
    reusable_transforms: dict,
    shortcuts: dict,
) -> list[dict]:
    """
    Return a list of edge-detail records for this mapping.
    Each record contains edge_id + full transformation_chain.
    """
    mapping_name = mapping.get("NAME", "")

    # ── Instance registry ──────────────────────────────────────────────
    instance_registry: dict[str, dict] = {}
    for inst in mapping.findall("INSTANCE"):
        inst_name = inst.get("NAME", "")
        inst_type = inst.get("TYPE", "")
        t_name = inst.get("TRANSFORMATION_NAME") or inst.get("MAPPINGNAME") or inst_name
        instance_registry[inst_name] = {
            "type": inst_type,
            "transformation_name": t_name,
            "db_name": inst.get("DBDNAME") or "",
        }

    # ── Inline transformations ─────────────────────────────────────────
    inline_transforms = _parse_transformations(mapping.findall("TRANSFORMATION"))

    # Merge: inline takes precedence for same name (mapping-level overrides reusable)
    all_transforms = {**reusable_transforms, **inline_transforms}

    # ── Helper: resolve instance → (db_schema, table, layer, elem_type) ──
    def get_table_info(inst_name: str):
        inst = instance_registry.get(inst_name, {})
        t_name = inst.get("transformation_name", inst_name)
        inst_type = inst.get("type", "")
        if t_name in shortcuts:
            t_name = shortcuts[t_name]["ref_object_name"]
        if inst_type == "SOURCE" or t_name in sources:
            src = sources.get(t_name, {})
            db_schema = src.get("db_schema", inst.get("db_name", "UNKNOWN"))
            layer = classify_layer(t_name, db_schema, "source")
            db_schema = normalise_schema(db_schema, t_name, layer)
            return db_schema, t_name, layer, "source"
        if inst_type == "TARGET" or t_name in targets:
            tgt = targets.get(t_name, {})
            db_schema = tgt.get("db_schema", "UNKNOWN")
            layer = classify_layer(t_name, db_schema, "target")
            db_schema = normalise_schema(db_schema, t_name, layer)
            return db_schema, t_name, layer, "target"
        return "TRANSFORM", t_name, "TRANSFORM", "transform"

    def get_t_info(inst_name: str):
        """Return (resolved_t_name, t_info_dict) for a transform instance."""
        inst = instance_registry.get(inst_name, {})
        t_name = inst.get("transformation_name", inst_name)
        return t_name, all_transforms.get(t_name, {})

    # ── Connectors & adjacency ─────────────────────────────────────────
    connectors = []
    for conn in mapping.findall("CONNECTOR"):
        connectors.append({
            "from_instance": conn.get("FROMINSTANCE", ""),
            "from_field"   : conn.get("FROMFIELD", ""),
            "to_instance"  : conn.get("TOINSTANCE", ""),
            "to_field"     : conn.get("TOFIELD", ""),
        })

    fwd: dict[tuple, list] = defaultdict(list)
    bwd: dict[tuple, list] = defaultdict(list)
    for c in connectors:
        kf = (c["from_instance"], c["from_field"])
        kt = (c["to_instance"], c["to_field"])
        fwd[kf].append(kt)
        bwd[kt].append(kf)

    # ── Input→output port resolution (same logic as extract_lineage.py) ──
    transform_port_fwd: dict[tuple, list] = defaultdict(list)
    for inst_name, inst_info in instance_registry.items():
        if inst_info["type"] != "TRANSFORMATION":
            continue
        t_name, t_info = get_t_info(inst_name)
        if not t_info:
            continue
        fields = t_info.get("fields", {})
        t_type = t_info.get("type", "")
        output_ports = {f for (i, f) in fwd if i == inst_name}
        input_ports  = {f for (i, f) in bwd if i == inst_name}

        if t_type in PASSTHROUGH_TYPES:
            for ip in input_ports:
                for op in output_ports:
                    transform_port_fwd[(inst_name, ip)].append((inst_name, op))
            continue

        local_vars = {f for f in fields if f not in output_ports and f not in input_ports}
        local_var_inputs: dict[str, set] = {}
        for lv in local_vars:
            lv_expr = fields.get(lv, {}).get("expression", "")
            if lv_expr:
                local_var_inputs[lv] = {ip for ip in input_ports if ip in lv_expr}

        for oport in output_ports:
            expr = fields.get(oport, {}).get("expression", "")
            if not expr:
                continue
            for iport in input_ports:
                if iport in expr:
                    transform_port_fwd[(inst_name, iport)].append((inst_name, oport))
            for lv, lv_inputs in local_var_inputs.items():
                if lv in expr:
                    for iport in lv_inputs:
                        transform_port_fwd[(inst_name, iport)].append((inst_name, oport))

    # ── DFS that captures full path (chain of transform steps) ──────────
    def trace_paths(start_inst: str, start_field: str,
                    visited: set | None = None,
                    chain: list | None = None) -> list[tuple]:
        """
        Returns list of (tgt_inst, tgt_field, final_expr, chain_copy)
        where chain_copy is an ordered list of (inst, in_port, out_port) dicts
        for every intermediate transformation visited.
        """
        if visited is None:
            visited = set()
        if chain is None:
            chain = []

        key = (start_inst, start_field)
        if key in visited:
            return []
        visited = visited | {key}     # immutable copy so branches don't interfere

        results = []
        nexts = fwd.get(key, [])
        if not nexts:
            return []

        for (to_inst, to_field) in nexts:
            _, _, _, elem_type = get_table_info(to_inst)

            if elem_type in ("source", "target"):
                # Reached a real table field — record the edge
                t_name, t_info = get_t_info(start_inst)
                t_type = t_info.get("type", "") if t_info else ""
                expr = ""
                if t_info:
                    f_info = t_info.get("fields", {}).get(start_field, {})
                    expr = f_info.get("expression", "") if f_info else ""
                    if not expr:
                        expr = t_type or "PASS_THROUGH"
                results.append((to_inst, to_field, expr, list(chain)))
            else:
                # Intermediate transformation
                t_name, t_info = get_t_info(to_inst)
                t_type = t_info.get("type", "") if t_info else ""

                direct_nexts = fwd.get((to_inst, to_field), [])
                if direct_nexts:
                    step = {
                        "inst"      : to_inst,
                        "t_name"    : t_name,
                        "t_type"    : t_type,
                        "in_port"   : to_field,
                        "out_port"  : to_field,  # same port unless resolved below
                        "t_info"    : t_info,
                    }
                    deeper = trace_paths(to_inst, to_field, visited, chain + [step])
                    results.extend(deeper)
                else:
                    # Try input→output port resolution
                    resolved = transform_port_fwd.get((to_inst, to_field), [])
                    if resolved:
                        for (out_inst, out_field) in resolved:
                            step = {
                                "inst"    : to_inst,
                                "t_name"  : t_name,
                                "t_type"  : t_type,
                                "in_port" : to_field,
                                "out_port": out_field,
                                "t_info"  : t_info,
                            }
                            deeper = trace_paths(out_inst, out_field, visited, chain + [step])
                            results.extend(deeper)
                    else:
                        # Dead-end transform — still record it
                        step = {
                            "inst"    : to_inst,
                            "t_name"  : t_name,
                            "t_type"  : t_type,
                            "in_port" : to_field,
                            "out_port": to_field,
                            "t_info"  : t_info,
                        }
                        results.append((to_inst, to_field, t_type or "PASS_THROUGH", chain + [step]))
        return results

    # ── Build output records ────────────────────────────────────────────
    edge_details: dict[str, dict] = {}
    edge_ids_seen: set[str] = set()

    source_instances = {n for n, i in instance_registry.items() if i["type"] == "SOURCE"}

    for src_inst in source_instances:
        db_schema, table_name, layer, _ = get_table_info(src_inst)
        src_fields = {c["from_field"] for c in connectors if c["from_instance"] == src_inst}

        for src_field in src_fields:
            src_vertex_id = make_field_id(db_schema, table_name, src_field)

            paths = trace_paths(src_inst, src_field)
            for (tgt_inst, tgt_field, final_expr, raw_chain) in paths:
                tgt_db, tgt_table, tgt_layer, tgt_type = get_table_info(tgt_inst)
                if tgt_type == "transform":
                    continue  # skip dead-end transforms as edge targets
                tgt_vertex_id = make_field_id(tgt_db, tgt_table, tgt_field)
                edge_id = f"{src_vertex_id}__to__{tgt_vertex_id}__{mapping_name}"

                if edge_id in edge_ids_seen:
                    continue
                edge_ids_seen.add(edge_id)

                # Build ordered transformation_chain
                transformation_chain = []
                for step_num, step in enumerate(raw_chain, start=1):
                    t_info = step.get("t_info") or {}
                    transformation_chain.append(
                        _build_transform_step(
                            t_name    = step["t_name"],
                            t_type    = step["t_type"],
                            input_port= step["in_port"],
                            output_port= step["out_port"],
                            t_info    = t_info,
                            step      = step_num,
                        )
                    )

                # Derive convenient summary fields from the chain
                custom_sql = next(
                    (s["custom_sql"] for s in transformation_chain if s["custom_sql"]), ""
                )
                lookup_condition = next(
                    (s["lookup_condition"] for s in transformation_chain if s["lookup_condition"]), ""
                )
                filter_condition = next(
                    (s["filter_condition"] for s in transformation_chain if s["filter_condition"]), ""
                )
                update_strategy = next(
                    (s["update_strategy_expression"] for s in transformation_chain
                     if s["update_strategy_expression"]), ""
                )

                edge_details[edge_id] = {
                    # ── Relational key / FK columns ──────────────────
                    "edge_id"                    : edge_id,
                    "from_vertex"                : src_vertex_id,
                    "to_vertex"                  : tgt_vertex_id,
                    "mapping_name"               : mapping_name,
                    "folder_name"                : folder_name,
                    # ── Summary columns (queryable without unpacking chain) ──
                    "final_expression"           : final_expr,
                    "custom_sql"                 : custom_sql,
                    "lookup_condition"           : lookup_condition,
                    "filter_condition"           : filter_condition,
                    "update_strategy_expression" : update_strategy,
                    "transformation_steps_count" : len(transformation_chain),
                    # ── Full chain detail ────────────────────────────
                    "transformation_chain"       : transformation_chain,
                }

    return list(edge_details.values())


# ---------------------------------------------------------------------------
# Folder-level
# ---------------------------------------------------------------------------

def extract_folder_details(folder: ET.Element) -> list[dict]:
    folder_name = folder.get("NAME", "")
    sources               = parse_sources(folder)
    targets               = parse_targets(folder)
    reusable_transforms   = parse_reusable_transformations(folder)
    shortcuts             = parse_shortcuts(folder)

    all_records: dict[str, dict] = {}
    for mapping in folder.findall("MAPPING"):
        for rec in extract_mapping_details(
            mapping, folder_name, sources, targets, reusable_transforms, shortcuts
        ):
            all_records[rec["edge_id"]] = rec

    return list(all_records.values())


# ---------------------------------------------------------------------------
# Optional: filter to only edges that exist in an existing lineage JSON
# ---------------------------------------------------------------------------

def filter_to_known_edges(records: list[dict], lineage_path: Path) -> list[dict]:
    """Keep only records whose edge_id appears in the lineage JSON edges list."""
    with open(lineage_path, encoding="utf-8") as f:
        lineage = json.load(f)
    known_ids = {e["id"] for e in lineage.get("edges", [])}
    return [r for r in records if r["edge_id"] in known_ids]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Extract transformation logic details per edge from Informatica XML"
    )
    parser.add_argument("--xml",     required=True, help="Path to Informatica XML export")
    parser.add_argument("--output",  required=True, help="Path to output transformation-details JSON")
    parser.add_argument("--lineage", default=None,
                        help="(Optional) Existing lineage JSON — output only edges present there")
    args = parser.parse_args()

    xml_path     = Path(args.xml)
    output_path  = Path(args.output)
    lineage_path = Path(args.lineage) if args.lineage else None

    if not xml_path.exists():
        print(f"ERROR: XML file not found: {xml_path}", file=sys.stderr)
        sys.exit(1)
    if lineage_path and not lineage_path.exists():
        print(f"ERROR: Lineage JSON not found: {lineage_path}", file=sys.stderr)
        sys.exit(1)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Parsing: {xml_path}")
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
    except ET.ParseError as e:
        print(f"ERROR: Failed to parse XML: {e}", file=sys.stderr)
        sys.exit(1)

    all_records: dict[str, dict] = {}
    mapping_count = 0

    for repo in root.findall("REPOSITORY"):
        for folder in repo.findall("FOLDER"):
            for rec in extract_folder_details(folder):
                all_records[rec["edge_id"]] = rec
            mapping_count += len(folder.findall("MAPPING"))

    records = list(all_records.values())

    if lineage_path:
        before = len(records)
        records = filter_to_known_edges(records, lineage_path)
        print(f"  Filtered to known edges: {before} → {len(records)}")

    output = {
        "source_file"   : str(xml_path),
        "stats": {
            "mappings_parsed"       : mapping_count,
            "edge_detail_records"   : len(records),
        },
        # One record per edge — index by edge_id for O(1) lookup
        "transformation_details": records,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"Done.")
    print(f"  Mappings parsed         : {mapping_count}")
    print(f"  Edge detail records     : {len(records)}")
    print(f"  Output written          : {output_path}")
    print()
    print("Relational table mapping:")
    print("  edge_transformation_details  ← top-level fields of each record (PK = edge_id)")
    print("  edge_transformation_steps    ← transformation_chain array   (FK = edge_id, PK = edge_id + step)")


if __name__ == "__main__":
    main()

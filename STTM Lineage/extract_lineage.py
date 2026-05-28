"""
extract_lineage.py
------------------
Parses Informatica PowerCenter XML export files and extracts field-level lineage
across TPR → TT → DDM layers.

Outputs a JSON file with:
  - vertices: field nodes (id = DB_SCHEMA.TABLE_NAME.FIELD_NAME, layer = TPR/TT/DDM)
  - edges:    transforms_to edges (source field → target field, with expression and mapping name)

Usage:
  python extract_lineage.py --xml "Input XML/wf_TPR_to_DDM_SHAW_sample.XML" --output "Output Files/sample_lineage.json"
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from xml.etree import ElementTree as ET


# ---------------------------------------------------------------------------
# Layer classification helpers
# ---------------------------------------------------------------------------

TT_PREFIXES = ("TT_", "WRK_")
DDM_SCHEMAS = ("CRDM_DDM", "NCNO_DDM", "DDM", "ADDX_DDM")
TT_SCHEMAS = ("CRDM_TMP", "NCNO_TMP")

# Schemas that are actually connection/database types, not real schema names
GENERIC_SCHEMAS = {"ORACLE", "FLAT FILE", "UNKNOWN", ""}

# Default DDM schema to assign when a target is classified DDM but has no real schema
DEFAULT_DDM_SCHEMA = "CRDM_DDM"
DEFAULT_TT_SCHEMA = "CRDM_TMP"


def normalise_schema(db_schema: str, table_name: str, layer: str) -> str:
    """
    If db_schema is a generic placeholder (e.g. 'Oracle'), infer the correct schema
    from the layer classification and table name. Always returns uppercase.
    """
    s = (db_schema or "").upper()
    if s in GENERIC_SCHEMAS:
        if layer == "TT":
            return DEFAULT_TT_SCHEMA
        if layer == "DDM":
            return DEFAULT_DDM_SCHEMA
    return s  # uppercase the real schema too for consistency


def classify_layer(table_name: str, db_schema: str | None, element_type: str) -> str:
    """
    Classify a table into TPR / TT / DDM layer.
    element_type: 'source' | 'target'
    """
    t = (table_name or "").upper()
    s = (db_schema or "").upper()
    if t.startswith(TT_PREFIXES):
        return "TT"
    if any(t.startswith(p) for p in TT_PREFIXES):
        return "TT"
    if s in DDM_SCHEMAS or any(ddm in s for ddm in DDM_SCHEMAS):
        return "DDM"
    if element_type == "target" and not t.startswith(TT_PREFIXES):
        # Targets that are not TT_ are typically DDM unless schema says otherwise
        return "DDM"
    if element_type == "source":
        return "TPR"
    return "UNKNOWN"


def make_field_id(db_schema: str, table_name: str, field_name: str) -> str:
    return f"{db_schema.upper()}.{table_name.upper()}.{field_name.upper()}"


# ---------------------------------------------------------------------------
# XML parsing helpers
# ---------------------------------------------------------------------------

def parse_sources(folder: ET.Element) -> dict:
    """Return {source_name: {db_schema, fields: {field_name: {datatype, precision}}}}"""
    sources = {}
    for src in folder.findall("SOURCE"):
        name = src.get("NAME", "")
        db_schema = src.get("DBDNAME") or src.get("OWNERNAME") or "UNKNOWN"
        layer = classify_layer(name, db_schema, "source")
        db_schema = normalise_schema(db_schema, name, layer)
        fields = {}
        for f in src.findall("SOURCEFIELD"):
            fname = f.get("NAME", "")
            fields[fname] = {
                "datatype": f.get("DATATYPE", ""),
                "precision": f.get("PRECISION", ""),
            }
        sources[name] = {"db_schema": db_schema, "table_name": name, "fields": fields, "element_type": "source"}
    return sources


def parse_targets(folder: ET.Element) -> dict:
    """Return {target_name: {db_schema, fields: {field_name: {datatype, precision}}}}"""
    targets = {}
    for tgt in folder.findall("TARGET"):
        name = tgt.get("NAME", "")
        db_schema = tgt.get("DATABASETYPE") or "UNKNOWN"
        # Try to get real schema from description or name context
        owner = tgt.get("OWNERNAME") or ""
        if owner:
            db_schema = owner
        # Normalise generic schemas (e.g. "Oracle") to real DDM/TT schema
        layer = classify_layer(name, db_schema, "target")
        db_schema = normalise_schema(db_schema, name, layer)
        fields = {}
        for f in tgt.findall("TARGETFIELD"):
            fname = f.get("NAME", "")
            fields[fname] = {
                "datatype": f.get("DATATYPE", ""),
                "precision": f.get("PRECISION", ""),
            }
        targets[name] = {"db_schema": db_schema, "table_name": name, "fields": fields, "element_type": "target"}
    return targets


def parse_reusable_transformations(folder: ET.Element) -> dict:
    """Return {transformation_name: {type, fields: {name: expression}}}"""
    transforms = {}
    for t in folder.findall("TRANSFORMATION"):
        name = t.get("NAME", "")
        t_type = t.get("TYPE", "")
        fields = {}
        for tf in t.findall("TRANSFORMFIELD"):
            fname = tf.get("NAME", "")
            expr = tf.get("EXPRESSION") or tf.get("DEFAULTVALUE") or ""
            fields[fname] = {"expression": expr, "datatype": tf.get("DATATYPE", "")}
        transforms[name] = {"type": t_type, "fields": fields}
    return transforms


def parse_shortcuts(folder: ET.Element) -> dict:
    """Return {shortcut_name: {ref_object_name, folder_name, object_type}}"""
    shortcuts = {}
    for sc in folder.findall("SHORTCUT"):
        name = sc.get("NAME", "")
        shortcuts[name] = {
            "ref_object_name": sc.get("REFOBJECTNAME", ""),
            "folder_name": sc.get("FOLDERNAME", ""),
            "object_type": sc.get("OBJECTTYPE", ""),
            "object_subtype": sc.get("OBJECTSUBTYPE", ""),
        }
    return shortcuts


def parse_mapplets(folder: ET.Element) -> dict:
    """Return {mapplet_name: {transformations}} — simplified extraction."""
    mapplets = {}
    for mpl in folder.findall("MAPPLET"):
        name = mpl.get("NAME", "")
        t_fields = {}
        for t in mpl.findall(".//TRANSFORMATION"):
            t_name = t.get("NAME", "")
            t_type = t.get("TYPE", "")
            for tf in t.findall("TRANSFORMFIELD"):
                fname = tf.get("NAME", "")
                expr = tf.get("EXPRESSION") or ""
                t_fields[f"{t_name}.{fname}"] = {"expression": expr, "t_type": t_type}
        mapplets[name] = {"fields": t_fields}
    return mapplets


# ---------------------------------------------------------------------------
# Mapping-level lineage extraction
# ---------------------------------------------------------------------------

def extract_mapping_lineage(
    mapping: ET.Element,
    folder_name: str,
    sources: dict,
    targets: dict,
    reusable_transforms: dict,
    shortcuts: dict,
) -> tuple[list, list]:
    """
    Extract (vertices, edges) from a single MAPPING element.
    Returns lists of vertex dicts and edge dicts.
    """
    mapping_name = mapping.get("NAME", "")
    vertices: dict[str, dict] = {}
    edges: list[dict] = []

    # Build instance registry: instance_name → {type, transformation_name, table_name}
    instance_registry: dict[str, dict] = {}
    for inst in mapping.findall("INSTANCE"):
        inst_name = inst.get("NAME", "")
        inst_type = inst.get("TYPE", "")  # SOURCE, TARGET, TRANSFORMATION
        t_name = inst.get("TRANSFORMATION_NAME") or inst.get("MAPPINGNAME") or inst_name
        db_name = inst.get("DBDNAME") or ""
        instance_registry[inst_name] = {
            "type": inst_type,
            "transformation_name": t_name,
            "db_name": db_name,
        }

    # Collect inline transformations in this mapping
    inline_transforms: dict[str, dict] = {}
    for t in mapping.findall("TRANSFORMATION"):
        t_name = t.get("NAME", "")
        t_type = t.get("TYPE", "")
        fields = {}
        for tf in t.findall("TRANSFORMFIELD"):
            fname = tf.get("NAME", "")
            expr = tf.get("EXPRESSION") or tf.get("DEFAULTVALUE") or ""
            fields[fname] = {
                "expression": expr,
                "datatype": tf.get("DATATYPE", ""),
                "precision": tf.get("PRECISION", ""),
            }
        # Also check TABLE_ATTRIBUTE for lookup conditions
        lookup_cond = ""
        for ta in t.findall("TABLEATTRIBUTE"):
            if ta.get("NAME") in ("Lookup condition", "Lookup Condition"):
                lookup_cond = ta.get("VALUE", "")
        inline_transforms[t_name] = {"type": t_type, "fields": fields, "lookup_condition": lookup_cond}

    # Merge reusable transforms (they can be referenced by instance TRANSFORMATION_NAME)
    all_transforms = {**reusable_transforms, **inline_transforms}

    def get_table_info(inst_name: str) -> tuple[str, str, str, str]:
        """Returns (db_schema, table_name, layer, element_type)"""
        inst = instance_registry.get(inst_name, {})
        t_name = inst.get("transformation_name", inst_name)
        inst_type = inst.get("type", "")

        # Check shortcut resolution
        if t_name in shortcuts:
            sc = shortcuts[t_name]
            resolved_name = sc["ref_object_name"]
            t_name = resolved_name

        if inst_type == "SOURCE" or (t_name in sources):
            src = sources.get(t_name, {})
            db_schema = src.get("db_schema", inst.get("db_name", "UNKNOWN"))
            layer = classify_layer(t_name, db_schema, "source")
            db_schema = normalise_schema(db_schema, t_name, layer)
            return db_schema, t_name, layer, "source"
        elif inst_type == "TARGET" or (t_name in targets):
            tgt = targets.get(t_name, {})
            db_schema = tgt.get("db_schema", "UNKNOWN")
            layer = classify_layer(t_name, db_schema, "target")
            db_schema = normalise_schema(db_schema, t_name, layer)
            return db_schema, t_name, layer, "target"
        else:
            # Transformation instance
            return "TRANSFORM", t_name, "TRANSFORM", "transform"

    def get_field_expression(inst_name: str, field_name: str, t_type: str = "") -> str:
        """Try to get expression for a field from a transformation instance."""
        inst = instance_registry.get(inst_name, {})
        t_name = inst.get("transformation_name", inst_name)
        t_info = all_transforms.get(t_name) or {}
        if t_info:
            f_info = t_info.get("fields", {}).get(field_name, {})
            expr = f_info.get("expression", "") if f_info else ""
            if expr:
                return expr
            # Return type as expression label when no explicit expression
            return t_info.get("type", "PASS_THROUGH") or "PASS_THROUGH"
        return "PASS_THROUGH"

    # Build connectivity graph: traverse CONNECTOR chain
    # CONNECTOR: FROMINSTANCE.FROMFIELD → TOINSTANCE.TOFIELD
    connectors = []
    for conn in mapping.findall("CONNECTOR"):
        connectors.append({
            "from_instance": conn.get("FROMINSTANCE", ""),
            "from_field": conn.get("FROMFIELD", ""),
            "to_instance": conn.get("TOINSTANCE", ""),
            "to_field": conn.get("TOFIELD", ""),
        })

    # Build adjacency: {(inst, field): [(to_inst, to_field), ...]}
    fwd: dict[tuple, list] = defaultdict(list)
    bwd: dict[tuple, list] = defaultdict(list)
    for c in connectors:
        key_from = (c["from_instance"], c["from_field"])
        key_to = (c["to_instance"], c["to_field"])
        fwd[key_from].append(key_to)
        bwd[key_to].append(key_from)

    # Build input→output port mapping for transformations.
    # For each transformation instance, map input port names to the output port(s)
    # whose expression references them — either directly or through local variables.
    # For Lookup/Stored Procedure transforms with no expressions, all input ports
    # are assumed to contribute to all output ports (lookup condition drives output).
    PASSTHROUGH_TYPES = {"Lookup Procedure", "Stored Procedure"}
    transform_port_fwd: dict[tuple, list] = defaultdict(list)
    for inst_name, inst_info in instance_registry.items():
        if inst_info["type"] != "TRANSFORMATION":
            continue
        t_name = inst_info.get("transformation_name", inst_name)
        t_info = all_transforms.get(t_name)
        if not t_info:
            continue
        fields = t_info.get("fields", {})
        t_type = t_info.get("type", "")
        # Identify output ports: those that have outgoing connectors from this instance
        output_ports = {f for (i, f) in fwd if i == inst_name}
        # Identify input ports: those that have incoming connectors to this instance
        input_ports = {f for (i, f) in bwd if i == inst_name}

        # For Lookups/SPs: input ports drive all output ports (no expression-based resolution)
        if t_type in PASSTHROUGH_TYPES:
            for iport in input_ports:
                for oport in output_ports:
                    transform_port_fwd[(inst_name, iport)].append((inst_name, oport))
            continue

        # Identify local variable ports (v_ prefix or PORTTYPE contains LOCAL VARIABLE)
        local_vars = {f for f in fields if f not in output_ports and f not in input_ports}

        # Build: which input ports does each local var reference?
        local_var_inputs: dict[str, set] = {}
        for lv in local_vars:
            lv_expr = fields.get(lv, {}).get("expression", "")
            if lv_expr:
                local_var_inputs[lv] = {ip for ip in input_ports if ip in lv_expr}

        # For each output port, find which input ports feed it (directly or via locals)
        for oport in output_ports:
            f_info = fields.get(oport, {})
            expr = f_info.get("expression", "")
            if not expr:
                continue
            # Direct input port references in the output expression
            for iport in input_ports:
                if iport in expr:
                    transform_port_fwd[(inst_name, iport)].append((inst_name, oport))
            # Indirect: output references a local variable which references input ports
            for lv, lv_inputs in local_var_inputs.items():
                if lv in expr:
                    for iport in lv_inputs:
                        transform_port_fwd[(inst_name, iport)].append((inst_name, oport))

    def add_field_vertex(inst_name: str, field_name: str) -> str | None:
        db_schema, table_name, layer, elem_type = get_table_info(inst_name)
        if elem_type == "transform":
            return None  # Intermediate transform nodes are not independent field vertices
        fid = make_field_id(db_schema, table_name, field_name)
        if fid not in vertices:
            vertices[fid] = {
                "id": fid,
                "label": "field",
                "db_schema": db_schema,
                "table_name": table_name,
                "field_name": field_name.upper(),
                "layer": layer,
                "data_type": "",
                "precision": "",
            }
            # Enrich with type info from source/target definitions
            if table_name in sources:
                fdef = sources[table_name]["fields"].get(field_name, {})
                vertices[fid]["data_type"] = fdef.get("datatype", "")
                vertices[fid]["precision"] = fdef.get("precision", "")
            elif table_name in targets:
                fdef = targets[table_name]["fields"].get(field_name, {})
                vertices[fid]["data_type"] = fdef.get("datatype", "")
                vertices[fid]["precision"] = fdef.get("precision", "")
        return fid

    def get_transformation_info(inst_name: str) -> tuple[str, str]:
        """Returns (transformation_name, transformation_type) for a transform instance."""
        inst = instance_registry.get(inst_name, {})
        t_name = inst.get("transformation_name", inst_name)
        t_info = all_transforms.get(t_name) or {}
        return t_name, t_info.get("type", inst.get("type", "Unknown"))

    # Walk connections: for each SOURCE field, trace to TARGET fields
    # We use a DFS that skips through intermediate transform nodes
    def trace_to_targets(start_inst: str, start_field: str, visited: set | None = None) -> list[tuple]:
        """
        Returns list of (target_inst, target_field, expression, path_transforms)
        where path_transforms is list of (transform_name, transform_type) traversed.
        """
        if visited is None:
            visited = set()
        key = (start_inst, start_field)
        if key in visited:
            return []
        visited.add(key)

        results = []
        nexts = fwd.get(key, [])
        for (to_inst, to_field) in nexts:
            db_schema, table_name, layer, elem_type = get_table_info(to_inst)
            if elem_type in ("source", "target"):
                # Reached a concrete field
                expr = get_field_expression(start_inst, start_field)
                t_name, t_type = get_transformation_info(start_inst)
                results.append((to_inst, to_field, expr, t_name, t_type))
            else:
                # Intermediate transform: get expression from this transform's output field
                expr = get_field_expression(to_inst, to_field)
                t_name, t_type = get_transformation_info(to_inst)
                # If this is an input port with no direct outgoing connectors,
                # resolve to the output port(s) that reference it via expression
                direct_nexts = fwd.get((to_inst, to_field), [])
                if direct_nexts:
                    deeper = trace_to_targets(to_inst, to_field, visited)
                else:
                    # Try input→output port resolution
                    resolved_outputs = transform_port_fwd.get((to_inst, to_field), [])
                    deeper = []
                    for (out_inst, out_field) in resolved_outputs:
                        expr = get_field_expression(out_inst, out_field)
                        deeper.extend(trace_to_targets(out_inst, out_field, visited))
                if deeper:
                    for (d_inst, d_field, d_expr, d_t_name, d_t_type) in deeper:
                        # Prefer the expression closest to the output
                        final_expr = d_expr if d_expr and d_expr != "PASS_THROUGH" else expr
                        results.append((d_inst, d_field, final_expr, d_t_name, d_t_type))
                else:
                    results.append((to_inst, to_field, expr, t_name, t_type))
        return results

    # Identify source instances (type = SOURCE)
    source_instances = {
        name for name, info in instance_registry.items()
        if info["type"] == "SOURCE"
    }

    for src_inst in source_instances:
        db_schema, table_name, layer, _ = get_table_info(src_inst)
        # Get all fields that appear in connectors from this source
        src_fields_in_connectors = {
            c["from_field"] for c in connectors if c["from_instance"] == src_inst
        }
        for src_field in src_fields_in_connectors:
            src_vertex_id = add_field_vertex(src_inst, src_field)
            if not src_vertex_id:
                continue
            targets_reached = trace_to_targets(src_inst, src_field)
            for (tgt_inst, tgt_field, expr, t_name, t_type) in targets_reached:
                tgt_vertex_id = add_field_vertex(tgt_inst, tgt_field)
                if not tgt_vertex_id:
                    continue
                edge_id = f"{src_vertex_id}__to__{tgt_vertex_id}__{mapping_name}"
                edges.append({
                    "id": edge_id,
                    "label": "transforms_to",
                    "from_vertex": src_vertex_id,
                    "to_vertex": tgt_vertex_id,
                    "mapping_name": mapping_name,
                    "folder_name": folder_name,
                    "transformation_name": t_name,
                    "transformation_type": t_type,
                    "expression": expr,
                })

    return list(vertices.values()), edges


# ---------------------------------------------------------------------------
# Folder-level parsing
# ---------------------------------------------------------------------------

def extract_folder_lineage(folder: ET.Element) -> tuple[list, list]:
    folder_name = folder.get("NAME", "")
    sources = parse_sources(folder)
    targets = parse_targets(folder)
    reusable_transforms = parse_reusable_transformations(folder)
    shortcuts = parse_shortcuts(folder)

    all_vertices: dict[str, dict] = {}
    all_edges: list[dict] = []
    edge_ids_seen: set[str] = set()

    for mapping in folder.findall("MAPPING"):
        v_list, e_list = extract_mapping_lineage(
            mapping, folder_name, sources, targets, reusable_transforms, shortcuts
        )
        for v in v_list:
            all_vertices[v["id"]] = v
        for e in e_list:
            if e["id"] not in edge_ids_seen:
                all_edges.append(e)
                edge_ids_seen.add(e["id"])

    return list(all_vertices.values()), all_edges


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Extract Informatica field-level lineage from PowerCenter XML")
    parser.add_argument("--xml", required=True, help="Path to Informatica XML export file")
    parser.add_argument("--output", required=True, help="Path to output JSON lineage file")
    args = parser.parse_args()

    xml_path = Path(args.xml)
    output_path = Path(args.output)

    if not xml_path.exists():
        print(f"ERROR: XML file not found: {xml_path}", file=sys.stderr)
        sys.exit(1)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Parsing: {xml_path}")
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
    except ET.ParseError as e:
        print(f"ERROR: Failed to parse XML: {e}", file=sys.stderr)
        sys.exit(1)

    all_vertices: dict[str, dict] = {}
    all_edges: list[dict] = []
    edge_ids_seen: set[str] = set()
    mapping_count = 0

    for repo in root.findall("REPOSITORY"):
        for folder in repo.findall("FOLDER"):
            v_list, e_list = extract_folder_lineage(folder)
            for v in v_list:
                all_vertices[v["id"]] = v
            for e in e_list:
                if e["id"] not in edge_ids_seen:
                    all_edges.append(e)
                    edge_ids_seen.add(e["id"])
            mapping_count += len(folder.findall("MAPPING"))

    output = {
        "source_file": str(xml_path),
        "stats": {
            "mappings": mapping_count,
            "field_vertices": len(all_vertices),
            "lineage_edges": len(all_edges),
        },
        "vertices": list(all_vertices.values()),
        "edges": all_edges,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"Done.")
    print(f"  Mappings parsed : {mapping_count}")
    print(f"  Field vertices  : {len(all_vertices)}")
    print(f"  Lineage edges   : {len(all_edges)}")
    print(f"  Output written  : {output_path}")


if __name__ == "__main__":
    main()

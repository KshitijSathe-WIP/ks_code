"""
extract_mapping_metadata.py
----------------------------
Parses Informatica PowerCenter XML and extracts per-mapping metadata into a
separate JSON, which is then loaded into a new Cosmos DB container
'mapping_metadata'.

Each document captures everything needed by the SQL backfill generator
that is NOT in the transformation_details edges:
  - UDF/EXPRMACRO definitions (to expand :UDF.TRIM(), UDF_SHAW_TO_DATE() etc.)
  - Mapping variables  ($$APPL default values)
  - Session config     (INSERT vs UPDATE, commit interval, connection names)

Output — one record per unique mapping name:
  {
    "id"               : "m_TMP_to_TMP_F_PARTICIPANTS",
    "mapping_name"     : "m_TMP_to_TMP_F_PARTICIPANTS",
    "folder_name"      : "2_CRDM_DIS",
    "udfs"             : { "TRIM": "LTRIM(RTRIM(IN_STRING))", ... },
    "mapping_variables": { "$$APPL": "" },
    "session"          : {
        "session_name"        : "s_m_TMP_to_TMP_F_PARTICIPANTS",
        "description"         : "...",
        "treat_source_rows_as": "Insert",
        "commit_interval"     : "10000",
        "connection_map"      : { "lkp_D_LOAN_ACCOUNT": "$DBConnection_CRDM_DDM", ... }
    }
  }

Usage:
  python extract_mapping_metadata.py \\
      --xml    "Input XML/wf_TPR_to_DDM_SHAW_sample.XML" \\
      --output "Output Files/mapping_metadata.json"
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from xml.etree import ElementTree as ET


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def parse_udfs(folder: ET.Element) -> dict:
    """
    Parse all EXPRMACRO elements in the folder.
    Returns {udf_name: expression_body}.
    These are shared across all mappings in the folder.
    """
    udfs = {}
    for macro in folder.findall("EXPRMACRO"):
        name = macro.get("NAME", "").strip()
        expr = macro.get("EXPRESSION", "").strip()
        if name:
            udfs[name] = expr
    return udfs


def parse_mapping_variables(mapping: ET.Element) -> dict:
    """
    Parse MAPPINGVARIABLE elements for a mapping.
    Returns {variable_name: default_value}, e.g. {"$$APPL": ""}.
    """
    variables = {}
    for mv in mapping.findall("MAPPINGVARIABLE"):
        name = mv.get("NAME", "").strip()
        if name:
            variables[name] = mv.get("DEFAULTVALUE", "")
    return variables


def parse_session(session: ET.Element) -> dict:
    """
    Parse a SESSION element into a compact dict with the most useful fields
    for understanding how the mapping is run in production.
    """
    name = session.get("NAME", "")
    desc = session.get("DESCRIPTION", "")

    # Top-level session ATTRIBUTEs
    treat_source = ""
    commit_interval = ""
    for attr in session.findall("ATTRIBUTE"):
        attr_name = attr.get("NAME", "")
        attr_val = attr.get("VALUE", "")
        if attr_name == "Treat source rows as":
            treat_source = attr_val
        elif attr_name == "Commit Interval":
            commit_interval = attr_val

    # Per-transformation-instance connection and override attributes
    connection_map = {}     # instance_name → connection string
    sql_overrides = {}      # instance_name → SQL override (if any)
    target_load_types = {}  # instance_name → Insert / Update / Normal
    source_filters = {}     # instance_name → source filter override

    for inst_elem in session.findall("SESSTRANSFORMATIONINST"):
        inst_name = inst_elem.get("SINSTANCENAME", "")
        for attr in inst_elem.findall("ATTRIBUTE"):
            a_name = attr.get("NAME", "")
            a_val = attr.get("VALUE", "")
            if a_name == "Connection Information" and a_val:
                connection_map[inst_name] = a_val
            elif a_name in ("Sql Query", "User Defined Query") and a_val:
                sql_overrides[inst_name] = a_val
            elif a_name == "Target Load Type" and a_val:
                target_load_types[inst_name] = a_val
            elif a_name == "Source Filter" and a_val:
                source_filters[inst_name] = a_val

    result = {
        "session_name"        : name,
        "description"         : desc,
        "treat_source_rows_as": treat_source,
        "commit_interval"     : commit_interval,
        "connection_map"      : connection_map,
    }
    if sql_overrides:
        result["sql_overrides"] = sql_overrides
    if target_load_types:
        result["target_load_types"] = target_load_types
    if source_filters:
        result["source_filter_overrides"] = source_filters

    return result


# ---------------------------------------------------------------------------
# Folder-level extraction
# ---------------------------------------------------------------------------

def extract_folder_metadata(folder: ET.Element) -> list[dict]:
    """
    Extract one metadata record per mapping in this folder.
    """
    folder_name = folder.get("NAME", "")
    folder_udfs = parse_udfs(folder)

    # Build session index: mapping_name → session dict
    session_index: dict[str, dict] = {}
    for session in folder.findall("SESSION"):
        mapping_name = session.get("MAPPINGNAME", "")
        if mapping_name:
            session_index[mapping_name] = parse_session(session)

    records = []
    for mapping in folder.findall("MAPPING"):
        mapping_name = mapping.get("NAME", "")
        if not mapping_name:
            continue

        mapping_variables = parse_mapping_variables(mapping)
        session = session_index.get(mapping_name, {})

        record = {
            "id"               : mapping_name,          # Cosmos DB document id
            "mapping_name"     : mapping_name,
            "folder_name"      : folder_name,
            "udfs"             : folder_udfs,           # shared across folder
            "mapping_variables": mapping_variables,
            "session"          : session,
        }
        records.append(record)

    return records


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Extract per-mapping metadata (UDFs, variables, sessions) from Informatica XML"
    )
    parser.add_argument("--xml",    required=True, help="Path to Informatica XML export")
    parser.add_argument("--output", required=True, help="Path to output mapping_metadata.json")
    args = parser.parse_args()

    xml_path    = Path(args.xml)
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

    # Collect across all repositories and folders
    all_records: dict[str, dict] = {}
    folder_count = 0

    for repo in root.findall("REPOSITORY"):
        for folder in repo.findall("FOLDER"):
            folder_count += 1
            for rec in extract_folder_metadata(folder):
                # Merge records from multiple folders (mapping names are unique across folders)
                all_records[rec["mapping_name"]] = rec

    records = list(all_records.values())

    output = {
        "source_file": str(xml_path),
        "stats": {
            "folders_parsed" : folder_count,
            "mappings_found" : len(records),
        },
        "mapping_metadata": records,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"Done.")
    print(f"  Folders parsed   : {folder_count}")
    print(f"  Mappings found   : {len(records)}")
    print(f"  Output written   : {output_path}")

    # Print UDF summary
    all_udfs: set = set()
    for rec in records:
        all_udfs.update(rec.get("udfs", {}).keys())
    if all_udfs:
        print(f"  UDFs captured    : {', '.join(sorted(all_udfs))}")


if __name__ == "__main__":
    main()

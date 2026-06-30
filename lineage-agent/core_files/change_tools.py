# change_tools.py
# Agent-facing tool for submitting lineage changes via YAML patch file.
# ALL changes must be authored in a .yaml file first.
# If the user describes a change without a file path, show the template
# from the docstring and ask them to create the file.

import json
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

_STTM_DIR = Path(__file__).resolve().parent.parent.parent / "STTM Lineage"
if str(_STTM_DIR) not in sys.path:
    sys.path.insert(0, str(_STTM_DIR))


def submit_change_file(file_path: str, submitted_by: str = None, description: str = None) -> str:
    """
    Read a YAML patch file and submit all changes as a single DRAFT version.
    This is the ONLY way to submit lineage changes.

    IMPORTANT: Changes cannot be submitted by describing them in chat.
    The user must create a .yaml patch file and provide the file path.
    If no file path is given, show this template and ask for the file:

    --- YAML template ---
    description: "What is changing and why"
    submitted_by: "your.email@domain.com"
    changes:

      # Update an edge expression
      - operation: UPDATE
        entity: cosmos_edge
        edge_id: "SCHEMA.TABLE.FIELD__to__SCHEMA.TABLE.FIELD__m_MAPPING"
        property: final_expression  # or: custom_sql | lookup_condition | filter_condition
        new_value: "IIF(ISNULL(src.RATE), -1, src.RATE)"

      # Add a new linkage
      - operation: ADD
        entity: cosmos_edge
        from_id: "TPR.SOURCE_TABLE.SOURCE_FIELD"
        to_id:   "DDM.TARGET_TABLE.TARGET_FIELD"
        mapping_name: "M_MAPPING_NAME"
        folder_name:  "FOLDER"            # optional
        expression:   "src.SOURCE_FIELD"  # optional

      # Delete an existing linkage
      - operation: DELETE
        entity: cosmos_edge
        edge_id: "SCHEMA.TABLE.FIELD__to__SCHEMA.TABLE.FIELD__m_MAPPING"

      # Add a new field node
      - operation: ADD
        entity: neo4j_field
        field_id:   "CRDM_DDM.F_TABLE.NEW_COL"
        table_name: "F_TABLE"
        db_schema:  "CRDM_DDM"
        layer:      "DDM"             # TPR | TT | DDM
        data_type:  "VARCHAR2(50)"
        precision:  ""                # optional

      # Update a field property
      - operation: UPDATE
        entity: neo4j_field
        field_id: "CRDM_DDM.F_TABLE.COL"
        property: data_type           # or: precision | layer
        new_value: "NUMBER(18,6)"
    --- end template ---

    :param file_path: Path to the .yaml or .yml patch file.
    :param submitted_by: Submitter email. Overrides the value in the file.
    :param description: Change description. Overrides the value in the file.
    :return: JSON with version_id and submission status.
    :rtype: str
    """
    try:
        path = Path(file_path)
        if not path.exists():
            return json.dumps({
                "error": f"File not found: {file_path}",
                "suggestion": "Check the path. Relative paths resolve from the current working directory.",
            }, indent=2)

        if path.suffix.lower() not in (".yaml", ".yml"):
            return json.dumps({
                "error": f"Only .yaml / .yml files are accepted. Got: '{path.suffix}'",
                "suggestion": "Save the file with a .yaml extension.",
            }, indent=2)

        from change_manager import apply_patch_file

        version_id = apply_patch_file(
            file_path    = str(path.resolve()),
            submitted_by = submitted_by,
            description  = description,
        )

        return json.dumps({
            "status":     "submitted",
            "version_id": version_id,
            "file":       str(path.resolve()),
            "message":    f"Change request {version_id} created. Pending approval.",
            "next_step":  f"python version_manager.py approve --id {version_id} --by <approver>",
        }, indent=2)

    except SystemExit:
        return json.dumps({"error": "Validation failed. See console for details."}, indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, indent=2)

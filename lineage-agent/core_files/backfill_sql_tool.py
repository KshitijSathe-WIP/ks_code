# backfill_sql_tool.py
# ────────────────────────────────────────────────────────────
# Agent Tool: Generate Backfill SQL for a DDM Field
#
# Wraps generate_backfill_sql.py (lineage-agent/scripts/) so the
# web agent can call it as a registered tool function.
# ────────────────────────────────────────────────────────────

import sys
import json
from pathlib import Path

# Ensure the scripts/ folder is on sys.path so we can import
# generate_backfill_sql.py which was moved to lineage-agent/scripts/
_AGENT_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = _AGENT_ROOT / "scripts"
for _p in (str(_SCRIPTS_DIR), str(_AGENT_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from generate_backfill_sql import generate_backfill_sql, resolve_field_id, LineageDataAccess


def generate_backfill_sql_for_field(field_id: str) -> str:
    """
    Generates a backfill SQL statement for a target DDM field by tracing its
    full upstream lineage through Neo4j (topology) and Cosmos DB (transformation
    logic), then assembling a CTE-based SELECT/JOIN/WHERE query from the chain.

    The SQL mirrors the Informatica transformation pipeline as closely as possible:
    each mapping becomes one CTE, expressions are translated from Informatica syntax
    to SQL, lookup conditions become LEFT JOINs, and filter conditions become WHERE clauses.

    Use this when the user asks:
    - "generate backfill SQL for field X"
    - "write a SQL query to populate / derive / backfill X"
    - "how would I load / insert data into X"
    - "give me the SQL for [SCHEMA.TABLE.FIELD]"

    :param field_id: The target field in SCHEMA.TABLE.FIELD or TABLE.FIELD format.
                     The function resolves the correct DDM node when given TABLE.FIELD.
                     Example: "CRDM_DDM.F_PARTICIPANTS.CUSTOMER_KEY"
                              "F_PARTICIPANTS.PARTICIPANT_KEY"
    :return: A CTE-based SQL statement as a plain string, ready to review and execute.
             Includes a header comment block identifying the target field, layer flow,
             and a warning that Informatica variables ($$VAR) must be replaced.
    :rtype: str
    """
    dao = LineageDataAccess()
    try:
        # Resolve TABLE.FIELD → full SCHEMA.TABLE.FIELD using Neo4j
        if field_id.count(".") == 1:
            table, field = field_id.split(".", 1)
            field_id = resolve_field_id(dao, field, table)
        elif field_id.count(".") == 0:
            return json.dumps({
                "error": "field_id must be in SCHEMA.TABLE.FIELD or TABLE.FIELD format.",
                "example": "CRDM_DDM.F_PARTICIPANTS.CUSTOMER_KEY"
            })

        sql = generate_backfill_sql(field_id.upper(), dao=dao)
        return sql
    except ValueError as exc:
        return json.dumps({"error": str(exc)})
    except Exception as exc:
        return json.dumps({"error": f"SQL generation failed: {exc}"})
    finally:
        dao.close()

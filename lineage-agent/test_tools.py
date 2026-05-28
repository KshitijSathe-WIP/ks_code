# test_tools.py
# ────────────────────────────────────────────────────────────────
# Test each lineage tool function independently before
# wiring them into the Foundry agent.
#
# Graph schema:
#   Node  : :Field  {id, db_schema, table_name, field_name, layer, data_type, precision}
#   Rel   : :TRANSFORMS_TO  {mapping_name, transformation_name, transformation_type, expression}
#   Layers: TPR (source) -> TT (temp/staging) -> DDM (data mart)
# ────────────────────────────────────────────────────────────────

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "core_files"))

from lineage_tools import (
    query_upstream_lineage,
    query_downstream_lineage,
    query_column_lineage,
    query_cross_layer_path,
    query_impact_analysis,
    query_tables_by_layer,
    run_custom_cypher,
    neo4j_client
)

def run_tests():
    print("=" * 60)
    print("LINEAGE TOOLS -- FUNCTION TESTS")
    print("=" * 60)

    # --- Test 1: Upstream ---
    print("\n[Test 1] Upstream Lineage of F_ACCOUNT_CONS")
    result = query_upstream_lineage("F_ACCOUNT_CONS")
    print(result)

    # --- Test 2: Downstream ---
    print("\n[Test 2] Downstream Lineage of MAST_LOAN_REC")
    result = query_downstream_lineage("MAST_LOAN_REC")
    print(result)

    # --- Test 3: Field-Level Lineage ---
    print("\n[Test 3] Field Lineage of CUSTOMER_KEY in TT_F_PARTICIPANTS")
    result = query_column_lineage("CUSTOMER_KEY", "TT_F_PARTICIPANTS")
    print(result)

    # --- Test 4: Cross-Layer Path ---
    print("\n[Test 4] Cross-Layer Path: TT_D_PARTICIPANT -> F_PARTICIPANTS")
    result = query_cross_layer_path("TT_D_PARTICIPANT", "F_PARTICIPANTS")
    print(result)

    # --- Test 5: Impact Analysis ---
    print("\n[Test 5] Impact Analysis of TT_D_PARTICIPANT")
    result = query_impact_analysis("TT_D_PARTICIPANT")
    print(result)

    # --- Test 6: Layer Inventory ---
    print("\n[Test 6] Tables in DDM Layer")
    result = query_tables_by_layer("DDM")
    print(result)

    # --- Test 7: Custom Cypher (safe read) ---
    print("\n[Test 7] Custom Cypher -- sample TPR fields")
    result = run_custom_cypher(
        "MATCH (f:Field {layer: 'TPR'}) RETURN DISTINCT f.table_name, f.db_schema LIMIT 10"
    )
    print(result)

    # --- Test 8: Custom Cypher (blocked write) ---
    print("\n[Test 8] Custom Cypher -- write attempt (should be blocked)")
    result = run_custom_cypher("CREATE (n:Field {field_name: 'HACK'}) RETURN n")
    print(result)

    # Cleanup
    neo4j_client.close()
    print("\n[Done] All tests complete!")


if __name__ == "__main__":
    run_tests()

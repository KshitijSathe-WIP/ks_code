# test_connection.py
# Quick test to verify Neo4j Aura connectivity

from neo4j_client import Neo4jLineageClient

def test():
    print("🔄 Connecting to Neo4j Aura...")
    
    # Using context manager (auto-closes on exit)
    with Neo4jLineageClient() as client:
        
        # ─── Test 1: Basic connectivity (already done in __init__) ───
        print("\n📋 Test 1: Connection verified ✅")

        # ─── Test 2: Count nodes ───
        result = client.run_cypher("MATCH (n) RETURN count(n) AS total_nodes")
        print(f"\n📋 Test 2: Node count → {result}")

        # ─── Test 3: List node labels ───
        result = client.run_cypher("CALL db.labels() YIELD label RETURN label")
        print(f"\n📋 Test 3: Node labels → {result}")

        # ─── Test 4: List relationship types ───
        result = client.run_cypher("CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType")
        print(f"\n📋 Test 4: Relationship types → {result}")

        # ─── Test 5: Sample lineage query (if data exists) ───
        result = client.run_cypher(
            "MATCH (t:Table) RETURN t.name AS table_name, t.layer AS layer LIMIT 5"
        )
        print(f"\n📋 Test 5: Sample tables → {result}")

    print("\n🎉 All tests passed! Neo4j connection utility is working.")


if __name__ == "__main__":
    test()
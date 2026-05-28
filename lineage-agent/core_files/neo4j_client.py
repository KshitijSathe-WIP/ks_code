# neo4j_client.py
# ────────────────────────────────────────────────────────
# Neo4j Aura Connection Utility for Lineage Agent
# Creates a singleton driver, manages connection pool,
# and provides safe read-only Cypher execution.
# ────────────────────────────────────────────────────────

import os
import json
from pathlib import Path
from dotenv import load_dotenv
from neo4j import GraphDatabase

# Load environment variables from .env file (project root)
load_dotenv(Path(__file__).resolve().parent.parent / ".env")


class Neo4jLineageClient:
    """
    Manages the connection to Neo4j Aura and executes 
    lineage Cypher queries safely.
    
    IMPORTANT: 
    - Create ONE instance and reuse it (driver has internal connection pool)
    - Always use execute_read() for lineage queries (routes to read replicas)
    - Always use $parameter placeholders (never f-strings — prevents injection)
    """

    def __init__(self):
        """Initialize the Neo4j driver and verify connectivity."""

        # ─── Step 1: Read credentials from environment ───
        self.uri = os.environ.get("NEO4J_URI")
        self.username = os.environ.get("NEO4J_USERNAME")
        self.password = os.environ.get("NEO4J_PASSWORD")

        # Validate that all credentials are present
        if not all([self.uri, self.username, self.password]):
            missing = []
            if not self.uri:
                missing.append("NEO4J_URI")
            if not self.username:
                missing.append("NEO4J_USERNAME")
            if not self.password:
                missing.append("NEO4J_PASSWORD")
            raise ValueError(
                f"Missing environment variables: {', '.join(missing)}. "
                f"Check your .env file."
            )

        # Rewrite neo4j+s:// -> neo4j+ssc:// to allow self-signed certs
        # (needed when a corporate SSL proxy intercepts the connection)
        uri_for_driver = self.uri.replace("neo4j+s://", "neo4j+ssc://", 1)

        # ─── Step 2: Create the driver (ONE instance, reuse everywhere) ───
        # The driver manages its own connection pool internally.
        # neo4j+ssc:// = encrypted, trusts self-signed/corporate-proxy certs
        self.driver = GraphDatabase.driver(
            uri_for_driver,
            auth=(self.username, self.password),
            max_connection_lifetime=3600,      # Recycle connections every hour
            max_connection_pool_size=50,        # Max 50 concurrent connections
            connection_acquisition_timeout=60   # Wait up to 60s for a connection
        )

        # ─── Step 3: Verify connectivity immediately ───
        # This forces the driver to create a connection NOW.
        # If URI/credentials are wrong, it fails fast here
        # instead of failing on the first query.
        try:
            self.driver.verify_connectivity()
            print("✅ Connected to Neo4j Aura successfully")
        except Exception as e:
            print(f"❌ Failed to connect to Neo4j Aura: {e}")
            raise

    def run_cypher(self, query: str, params: dict = None) -> str:
        """
        Execute a READ-ONLY Cypher query and return results as JSON string.

        Args:
            query:  A valid Cypher query string.
                    Use $param_name for parameters (NEVER use f-strings!)
            params: Dictionary of query parameters. Example: {"table_name": "CUSTOMER"}

        Returns:
            JSON string containing list of result records.
        
        Example:
            result = client.run_cypher(
                "MATCH (t:Table {name: $name}) RETURN t",
                {"name": "CUSTOMER_SNAPSHOT"}
            )
        """

        # Inner function used by execute_read for automatic retry
        def _execute(tx, cypher, parameters):
            result = tx.run(cypher, parameters or {})
            return [record.data() for record in result]

        try:
            # Use execute_read (NOT session.run):
            # - Routes to read replicas in a cluster
            # - Automatically retries on transient errors (deadlocks, leader switches)
            with self.driver.session(database="neo4j") as session:
                records = session.execute_read(_execute, query, params)
                return json.dumps(records, indent=2, default=str)

        except Exception as e:
            error_response = {
                "error": str(e),
                "query": query,
                "params": params
            }
            return json.dumps(error_response, indent=2)

    def get_schema(self) -> str:
        """
        Retrieve the graph schema (node labels, relationship types, properties).
        Useful for the LLM to understand what's in the graph.
        """
        schema_query = """
        CALL db.schema.visualization() 
        YIELD nodes, relationships
        RETURN nodes, relationships
        """
        return self.run_cypher(schema_query)

    def close(self):
        """Close the driver and release all connections."""
        if self.driver:
            self.driver.close()
            print("🔌 Neo4j driver closed")

    # ─── Context Manager Support ───
    # Allows: with Neo4jLineageClient() as client:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
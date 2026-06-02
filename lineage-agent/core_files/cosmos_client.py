# cosmos_client.py
# ────────────────────────────────────────────────────────────
# Azure Cosmos DB (NoSQL) Connection Utility for Lineage Agent
# Creates a singleton client, manages database/container access,
# and provides safe read/write operations.
#
# Connection string:
#   AccountEndpoint=https://td-bank-cosmos.documents.azure.com:443/
#   AccountKey loaded from COSMOS_KEY environment variable
# ────────────────────────────────────────────────────────────

import os
import logging
from pathlib import Path
from dotenv import load_dotenv
from azure.cosmos import CosmosClient, PartitionKey, exceptions

# Load environment variables from .env file
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logger = logging.getLogger(__name__)


class CosmosLineageClient:
    """
    Manages the connection to Azure Cosmos DB (NoSQL API) and provides
    safe document read/write operations for lineage data.

    IMPORTANT:
    - Create ONE instance and reuse it (client is thread-safe)
    - Credentials are read from environment variables:
        COSMOS_ENDPOINT  — the Cosmos DB account endpoint URL
        COSMOS_KEY       — the primary key (account key)
    """

    def __init__(self, database_name: str, container_name: str):
        """
        Initialize the Cosmos DB client and verify connectivity.

        :param database_name: Name of the Cosmos DB database to connect to.
        :param container_name: Name of the container (collection) to use.
        """

        # ─── Step 1: Read credentials from environment ───
        self.endpoint = os.environ.get("COSMOS_ENDPOINT")
        self.key = os.environ.get("COSMOS_KEY")

        if not self.endpoint or not self.key:
            missing = []
            if not self.endpoint:
                missing.append("COSMOS_ENDPOINT")
            if not self.key:
                missing.append("COSMOS_KEY")
            raise ValueError(
                f"Missing environment variables: {', '.join(missing)}. "
                f"Check your .env file."
            )

        # ─── Step 2: Create the CosmosClient ───
        self.client = CosmosClient(url=self.endpoint, credential=self.key)

        # ─── Step 3: Get database and container references ───
        self.database_name = database_name
        self.container_name = container_name

        try:
            self.database = self.client.get_database_client(database_name)
            self.container = self.database.get_container_client(container_name)
            # Verify the container exists by reading its properties
            self.container.read()
            logger.info(
                "Cosmos DB connected: database=%s, container=%s",
                database_name,
                container_name,
            )
        except exceptions.CosmosResourceNotFoundError as exc:
            raise ConnectionError(
                f"Cosmos DB resource not found — database='{database_name}', "
                f"container='{container_name}'. Verify names are correct."
            ) from exc

    # ─── Query ───────────────────────────────────────────────

    def query(self, sql: str, parameters: list | None = None) -> list[dict]:
        """
        Execute a parameterised SQL query and return all matching items.

        :param sql: Cosmos DB SQL query, e.g.
                    "SELECT * FROM c WHERE c.table_name = @table"
        :param parameters: List of {"name": "@param", "value": val} dicts.
        :return: List of document dicts.
        """
        kwargs: dict = {"query": sql, "enable_cross_partition_query": True}
        if parameters:
            kwargs["parameters"] = parameters
        return list(self.container.query_items(**kwargs))

    # ─── Upsert ──────────────────────────────────────────────

    def upsert(self, document: dict) -> dict:
        """
        Insert or update a document in the container.

        :param document: Dict representing the document. Must include 'id'.
        :return: The upserted document as returned by Cosmos DB.
        """
        if "id" not in document:
            raise ValueError("Document must include an 'id' field.")
        return self.container.upsert_item(document)

    # ─── Read single item ─────────────────────────────────────

    def get(self, item_id: str, partition_key: str) -> dict | None:
        """
        Read a single document by id and partition key.

        :param item_id: The document id.
        :param partition_key: The partition key value.
        :return: Document dict, or None if not found.
        """
        try:
            return self.container.read_item(item=item_id, partition_key=partition_key)
        except exceptions.CosmosResourceNotFoundError:
            return None

    # ─── Delete ──────────────────────────────────────────────

    def delete(self, item_id: str, partition_key: str) -> None:
        """
        Delete a document by id and partition key.

        :param item_id: The document id.
        :param partition_key: The partition key value.
        """
        self.container.delete_item(item=item_id, partition_key=partition_key)

    # ─── Create database/container if missing ────────────────

    def ensure_container(self, partition_key_path: str = "/id") -> None:
        """
        Create the database and container if they do not already exist.
        Safe to call on every startup.

        :param partition_key_path: Partition key path, e.g. "/table_name".
        """
        db = self.client.create_database_if_not_exists(id=self.database_name)
        db.create_container_if_not_exists(
            id=self.container_name,
            partition_key=PartitionKey(path=partition_key_path),
        )
        self.database = db
        self.container = db.get_container_client(self.container_name)
        logger.info(
            "Ensured container exists: %s/%s", self.database_name, self.container_name
        )

"""Cosmos DB client initialization and management."""
from typing import Optional
from azure.cosmos import CosmosClient, DatabaseProxy, ContainerProxy
from azure.identity import DefaultAzureCredential
from src.api.settings import settings
from src.common.logging import get_logger
from src.common.exceptions import CosmosDBException

logger = get_logger(__name__)


class CosmosDBClient:
    """
    Manages Cosmos DB client and provides access to containers.
    Uses DefaultAzureCredential for authentication (Azure CLI or Managed Identity).
    """
    
    def __init__(self):
        """Initialize the Cosmos DB client."""
        self._client: Optional[CosmosClient] = None
        self._database: Optional[DatabaseProxy] = None
        self._incident_container: Optional[ContainerProxy] = None
        self._change_container: Optional[ContainerProxy] = None
    
    def connect(self) -> None:
        """Establish connection to Cosmos DB."""
        try:
            logger.info(f"Connecting to Cosmos DB: {settings.azure_cosmos_endpoint}")
            
            # Use key authentication if provided, otherwise use DefaultAzureCredential
            if settings.azure_cosmos_key:
                logger.info("Using key authentication for Cosmos DB")
                self._client = CosmosClient(
                    url=settings.azure_cosmos_endpoint,
                    credential=settings.azure_cosmos_key
                )
            else:
                logger.info("Using DefaultAzureCredential for Cosmos DB")
                credential = DefaultAzureCredential()
                self._client = CosmosClient(
                    url=settings.azure_cosmos_endpoint,
                    credential=credential
                )
            
            # Get database reference
            self._database = self._client.get_database_client(settings.azure_cosmos_database)
            
            # Get container references
            self._incident_container = self._database.get_container_client(
                settings.azure_cosmos_incident_container
            )
            self._change_container = self._database.get_container_client(
                settings.azure_cosmos_change_container
            )
            
            logger.info("Successfully connected to Cosmos DB")
            
        except Exception as e:
            logger.error(f"Failed to connect to Cosmos DB: {e}", exc_info=True)
            raise CosmosDBException(f"Cosmos DB connection failed: {e}")
    
    @property
    def client(self) -> CosmosClient:
        """Get the Cosmos DB client."""
        if not self._client:
            self.connect()
        return self._client
    
    @property
    def database(self) -> DatabaseProxy:
        """Get the database proxy."""
        if not self._database:
            self.connect()
        return self._database
    
    @property
    def incident_container(self) -> ContainerProxy:
        """Get the historical incidents container."""
        if not self._incident_container:
            self.connect()
        return self._incident_container
    
    @property
    def change_container(self) -> ContainerProxy:
        """Get the change records container."""
        if not self._change_container:
            self.connect()
        return self._change_container
    
    def close(self) -> None:
        """Close the Cosmos DB connection."""
        if self._client:
            self._client.close()
            logger.info("Cosmos DB connection closed")


# Global client instance
_cosmos_client: Optional[CosmosDBClient] = None


def get_cosmos_client() -> CosmosDBClient:
    """
    Get or create the global Cosmos DB client instance.
    
    Returns:
        Initialized CosmosDBClient instance
    """
    global _cosmos_client
    if _cosmos_client is None:
        _cosmos_client = CosmosDBClient()
        _cosmos_client.connect()
    return _cosmos_client

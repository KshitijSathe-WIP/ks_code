"""Create Cosmos DB database and containers if they don't exist."""
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from azure.cosmos import PartitionKey
from azure.cosmos.exceptions import CosmosResourceExistsError
from src.cosmos.client import get_cosmos_client
from src.api.settings import settings
from src.common.logging import setup_logging, get_logger

setup_logging(settings.log_level)
logger = get_logger(__name__)


def create_database_and_containers():
    """Create the database and containers with proper partition keys."""
    try:
        cosmos_client = get_cosmos_client()
        client = cosmos_client.client
        
        # Create database
        logger.info(f"Creating database: {settings.azure_cosmos_database}")
        try:
            database = client.create_database(id=settings.azure_cosmos_database)
            logger.info(f"✓ Database created: {settings.azure_cosmos_database}")
        except CosmosResourceExistsError:
            logger.info(f"✓ Database already exists: {settings.azure_cosmos_database}")
            database = client.get_database_client(settings.azure_cosmos_database)
        
        # Create historical incidents container
        logger.info(f"Creating container: {settings.azure_cosmos_incident_container}")
        try:
            incident_container = database.create_container(
                id=settings.azure_cosmos_incident_container,
                partition_key=PartitionKey(path="/serviceKey")
            )
            logger.info(f"✓ Container created: {settings.azure_cosmos_incident_container}")
        except CosmosResourceExistsError:
            logger.info(f"✓ Container already exists: {settings.azure_cosmos_incident_container}")
        
        # Create change records container
        logger.info(f"Creating container: {settings.azure_cosmos_change_container}")
        try:
            change_container = database.create_container(
                id=settings.azure_cosmos_change_container,
                partition_key=PartitionKey(path="/serviceKey")
            )
            logger.info(f"✓ Container created: {settings.azure_cosmos_change_container}")
        except CosmosResourceExistsError:
            logger.info(f"✓ Container already exists: {settings.azure_cosmos_change_container}")
        
        logger.info("\n✓ Database and containers are ready!")
        return 0
        
    except Exception as e:
        logger.error(f"Error creating database/containers: {e}", exc_info=True)
        return 1


def main():
    """Main entry point."""
    logger.info("=" * 60)
    logger.info("Cosmos DB Database and Container Creation")
    logger.info("=" * 60)
    return create_database_and_containers()


if __name__ == "__main__":
    sys.exit(main())

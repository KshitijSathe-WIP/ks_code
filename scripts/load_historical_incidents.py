"""Load historical incidents into Cosmos DB."""
import sys
import json
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.cosmos.client import get_cosmos_client
from src.cosmos.repositories import IncidentRepository
from src.api.settings import settings
from src.common.logging import setup_logging, get_logger

setup_logging(settings.log_level)
logger = get_logger(__name__)


def load_incidents():
    """Load historical incidents from JSON file into Cosmos DB."""
    try:
        # Load JSON data
        data_file = Path(__file__).parent.parent / "data" / "historical_incidents.json"
        logger.info(f"Reading incidents from: {data_file}")
        
        with open(data_file, "r", encoding="utf-8") as f:
            incidents = json.load(f)
        
        logger.info(f"Loaded {len(incidents)} incidents from JSON")
        
        # Connect to Cosmos DB
        cosmos_client = get_cosmos_client()
        incident_repo = IncidentRepository(cosmos_client)
        
        # Load each incident
        loaded_count = 0
        skipped_count = 0
        
        for incident in incidents:
            incident_id = incident.get("id")
            service_key = incident.get("serviceKey")
            
            # Check if already exists
            existing = incident_repo.get_by_id(incident_id, service_key)
            if existing:
                logger.info(f"Skipping existing incident: {incident_id}")
                skipped_count += 1
                continue
            
            # Create incident
            incident_repo.create(incident)
            logger.info(f"✓ Loaded incident: {incident_id}")
            loaded_count += 1
        
        logger.info(f"\n✓ Load complete!")
        logger.info(f"  - Loaded: {loaded_count}")
        logger.info(f"  - Skipped (already exist): {skipped_count}")
        logger.info(f"  - Total in file: {len(incidents)}")
        
        return 0
        
    except Exception as e:
        logger.error(f"Error loading incidents: {e}", exc_info=True)
        return 1


def main():
    """Main entry point."""
    logger.info("=" * 60)
    logger.info("Load Historical Incidents to Cosmos DB")
    logger.info("=" * 60)
    return load_incidents()


if __name__ == "__main__":
    sys.exit(main())

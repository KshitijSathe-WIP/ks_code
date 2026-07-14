"""Load change records into Cosmos DB."""
import sys
import json
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.cosmos.client import get_cosmos_client
from src.cosmos.repositories import ChangeRepository
from src.api.settings import settings
from src.common.logging import setup_logging, get_logger

setup_logging(settings.log_level)
logger = get_logger(__name__)


def load_changes():
    """Load change records from JSON file into Cosmos DB."""
    try:
        # Load JSON data
        data_file = Path(__file__).parent.parent / "data" / "change_records.json"
        logger.info(f"Reading changes from: {data_file}")
        
        with open(data_file, "r", encoding="utf-8") as f:
            changes = json.load(f)
        
        logger.info(f"Loaded {len(changes)} changes from JSON")
        
        # Connect to Cosmos DB
        cosmos_client = get_cosmos_client()
        change_repo = ChangeRepository(cosmos_client)
        
        # Load each change
        loaded_count = 0
        skipped_count = 0
        
        for change in changes:
            change_id = change.get("id")
            service_key = change.get("serviceKey")
            
            # Check if already exists
            existing = change_repo.get_by_id(change_id, service_key)
            if existing:
                logger.info(f"Skipping existing change: {change_id}")
                skipped_count += 1
                continue
            
            # Create change
            change_repo.create(change)
            logger.info(f"✓ Loaded change: {change_id}")
            loaded_count += 1
        
        logger.info(f"\n✓ Load complete!")
        logger.info(f"  - Loaded: {loaded_count}")
        logger.info(f"  - Skipped (already exist): {skipped_count}")
        logger.info(f"  - Total in file: {len(changes)}")
        
        return 0
        
    except Exception as e:
        logger.error(f"Error loading changes: {e}", exc_info=True)
        return 1


def main():
    """Main entry point."""
    logger.info("=" * 60)
    logger.info("Load Change Records to Cosmos DB")
    logger.info("=" * 60)
    return load_changes()


if __name__ == "__main__":
    sys.exit(main())

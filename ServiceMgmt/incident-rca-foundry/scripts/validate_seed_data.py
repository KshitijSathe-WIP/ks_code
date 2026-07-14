"""Validate seed data integrity and relationships."""
import sys
import json
from pathlib import Path
from collections import defaultdict

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.common.logging import setup_logging, get_logger

setup_logging("INFO")
logger = get_logger(__name__)


def validate_seed_data():
    """Validate incidents and changes data integrity."""
    try:
        # Load JSON files
        data_dir = Path(__file__).parent.parent / "data"
        incidents_file = data_dir / "historical_incidents.json"
        changes_file = data_dir / "change_records.json"
        
        logger.info("Loading seed data files...")
        with open(incidents_file, "r", encoding="utf-8") as f:
            incidents = json.load(f)
        with open(changes_file, "r", encoding="utf-8") as f:
            changes = json.load(f)
        
        logger.info(f"Loaded {len(incidents)} incidents and {len(changes)} changes")
        
        # Build validation report
        issues = []
        warnings = []
        
        # 1. Check for duplicate IDs
        incident_ids = [i.get("id") for i in incidents]
        change_ids = [c.get("id") for c in changes]
        
        if len(incident_ids) != len(set(incident_ids)):
            issues.append("Duplicate incident IDs found")
        if len(change_ids) != len(set(change_ids)):
            issues.append("Duplicate change IDs found")
        
        # 2. Build change ID lookup
        change_id_lookup = {c.get("changeId"): c for c in changes}
        
        # 3. Validate linkedChangeId relationships
        orphaned_changes = []
        valid_links = 0
        
        for incident in incidents:
            linked_change_id = incident.get("linkedChangeId")
            if linked_change_id:
                if linked_change_id not in change_id_lookup:
                    orphaned_changes.append(f"{incident.get('incidentId')} -> {linked_change_id}")
                else:
                    valid_links += 1
        
        if orphaned_changes:
            issues.append(f"Orphaned change references: {', '.join(orphaned_changes)}")
        
        # 4. Check service key consistency
        service_keys = set()
        for incident in incidents:
            service_keys.add(incident.get("serviceKey"))
        for change in changes:
            service_keys.add(change.get("serviceKey"))
        
        # 5. Check required fields
        for idx, incident in enumerate(incidents):
            required_fields = ["id", "incidentId", "serviceKey", "businessService", "rootCause", "rootCauseCategory"]
            missing = [f for f in required_fields if not incident.get(f)]
            if missing:
                issues.append(f"Incident {idx}: Missing fields {missing}")
        
        for idx, change in enumerate(changes):
            required_fields = ["id", "changeId", "serviceKey", "businessService"]
            missing = [f for f in required_fields if not change.get(f)]
            if missing:
                issues.append(f"Change {idx}: Missing fields {missing}")
        
        # 6. Count incidents by service
        incidents_by_service = defaultdict(int)
        for incident in incidents:
            incidents_by_service[incident.get("businessService")] += 1
        
        changes_by_service = defaultdict(int)
        for change in changes:
            changes_by_service[change.get("businessService")] += 1
        
        # Print report
        logger.info("\n" + "=" * 60)
        logger.info("Validation Report")
        logger.info("=" * 60)
        
        logger.info(f"\nData Summary:")
        logger.info(f"  Total Incidents: {len(incidents)}")
        logger.info(f"  Total Changes: {len(changes)}")
        logger.info(f"  Valid linkedChangeId relationships: {valid_links}")
        logger.info(f"  Unique service keys: {len(service_keys)}")
        
        logger.info(f"\nIncidents by Service:")
        for service, count in sorted(incidents_by_service.items()):
            logger.info(f"  {service}: {count}")
        
        logger.info(f"\nChanges by Service:")
        for service, count in sorted(changes_by_service.items()):
            logger.info(f"  {service}: {count}")
        
        if issues:
            logger.error(f"\n✗ Validation Issues Found:")
            for issue in issues:
                logger.error(f"  - {issue}")
            return 1
        
        if warnings:
            logger.warning(f"\nWarnings:")
            for warning in warnings:
                logger.warning(f"  - {warning}")
        
        logger.info(f"\n✓ Validation passed! Data integrity confirmed.")
        logger.info(f"\nKey Test Cases:")
        
        # Find a good demo incident
        demo_incident = next((i for i in incidents if i.get("linkedChangeId")), None)
        if demo_incident:
            logger.info(f"  Demo Incident: {demo_incident.get('incidentId')}")
            logger.info(f"    Service: {demo_incident.get('businessService')}")
            logger.info(f"    Linked Change: {demo_incident.get('linkedChangeId')}")
        
        return 0
        
    except Exception as e:
        logger.error(f"Error validating data: {e}", exc_info=True)
        return 1


def main():
    """Main entry point."""
    logger.info("=" * 60)
    logger.info("Seed Data Validation")
    logger.info("=" * 60)
    return validate_seed_data()


if __name__ == "__main__":
    sys.exit(main())

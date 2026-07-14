"""Extract incident and change records from Excel file and convert to JSON seed data."""
import sys
import json
from pathlib import Path
import pandas as pd

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.common.logging import setup_logging, get_logger

setup_logging("INFO")
logger = get_logger(__name__)


def normalize_service_key(business_service: str) -> str:
    """
    Convert business service name to normalized service key.
    
    Args:
        business_service: Business service name
        
    Returns:
        Normalized service key (lowercase, hyphenated)
    """
    if not business_service or pd.isna(business_service):
        return "unknown"
    
    # Convert to lowercase and replace spaces with hyphens
    return business_service.lower().strip().replace(" ", "-").replace("_", "-")


def convert_to_list(value) -> list:
    """Convert a value to a list, handling various input types."""
    if pd.isna(value) or value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        # Split by comma or semicolon
        items = [item.strip() for item in value.replace(";", ",").split(",")]
        return [item for item in items if item]
    return [str(value)]


def extract_incidents(excel_file: Path) -> list:
    """
    Extract historical incidents from Excel file.
    
    Args:
        excel_file: Path to Excel file
        
    Returns:
        List of incident documents
    """
    logger.info("Reading incidents from Excel...")
    df = pd.read_excel(excel_file, sheet_name="HistoricalIncidents")
    
    incidents = []
    for idx, row in df.iterrows():
        # Normalize service key
        business_service = row.get("BusinessService", "Unknown")
        service_key = normalize_service_key(business_service)
        
        # Build incident document
        incident = {
            "id": row.get("IncidentID", f"INC{10000+idx}"),
            "documentType": "historicalIncident",
            "serviceKey": service_key,
            "incidentId": row.get("IncidentID", f"INC{10000+idx}"),
            "incidentTitle": row.get("IncidentTitle", ""),
            "incidentDescription": row.get("IncidentDescription", ""),
            "severity": row.get("Severity", "P3"),
            "businessService": business_service,
            "applicationName": row.get("ApplicationName", ""),
            "configurationItem": row.get("ConfigurationItem", ""),
            "symptoms": convert_to_list(row.get("Symptoms", "")),
            "errorCodes": convert_to_list(row.get("ErrorCodes", "")),
            "rootCause": row.get("RootCause", ""),
            "rootCauseCategory": row.get("RootCauseCategory", "Unknown"),
            "resolutionSummary": row.get("ResolutionSummary", ""),
            "linkedChangeId": row.get("LinkedChangeID", "") if not pd.isna(row.get("LinkedChangeID")) else None,
            "tags": convert_to_list(row.get("Tags", "")),
            "isResolved": True
        }
        
        # Build searchText field for keyword matching
        search_parts = [
            business_service,
            incident["applicationName"],
            incident["incidentTitle"],
            incident["incidentDescription"],
            " ".join(incident["symptoms"]),
            " ".join(incident["errorCodes"]),
            " ".join(incident["tags"]),
            incident["rootCause"],
            incident["configurationItem"]
        ]
        incident["searchText"] = " ".join([str(p) for p in search_parts if p]).lower()
        
        incidents.append(incident)
    
    logger.info(f"Extracted {len(incidents)} incidents")
    return incidents


def extract_changes(excel_file: Path) -> list:
    """
    Extract change records from Excel file.
    
    Args:
        excel_file: Path to Excel file
        
    Returns:
        List of change documents
    """
    logger.info("Reading change records from Excel...")
    df = pd.read_excel(excel_file, sheet_name="ChangeRecords")
    
    changes = []
    for idx, row in df.iterrows():
        # Normalize service key
        business_service = row.get("BusinessService", "Unknown")
        service_key = normalize_service_key(business_service)
        
        # Build change document
        change = {
            "id": row.get("ChangeID", f"CHG{50000+idx}"),
            "documentType": "changeRecord",
            "serviceKey": service_key,
            "changeId": row.get("ChangeID", f"CHG{50000+idx}"),
            "changeTitle": row.get("ChangeTitle", ""),
            "changeDescription": row.get("ChangeDescription", ""),
            "changeType": row.get("ChangeType", "Normal"),
            "changeCategory": row.get("ChangeCategory", "Other"),
            "changeStatus": row.get("ChangeStatus", "Completed"),
            "businessService": business_service,
            "applicationName": row.get("ApplicationName", ""),
            "configurationItem": row.get("ConfigurationItem", ""),
            "implementationSummary": row.get("ImplementationSummary", ""),
            "rollbackPerformed": bool(row.get("RollbackPerformed", False)),
            "validationResult": row.get("ValidationResult", "Successful"),
            "postImplementationIssues": convert_to_list(row.get("PostImplementationIssues", "")),
            "relatedIncidentIds": convert_to_list(row.get("RelatedIncidentIDs", "")),
            "changeCorrelationNotes": row.get("ChangeCorrelationNotes", ""),
            "tags": convert_to_list(row.get("Tags", ""))
        }
        
        # Build searchText field
        search_parts = [
            business_service,
            change["applicationName"],
            change["changeTitle"],
            change["changeDescription"],
            change["implementationSummary"],
            " ".join(change["tags"]),
            change["configurationItem"]
        ]
        change["searchText"] = " ".join([str(p) for p in search_parts if p]).lower()
        
        changes.append(change)
    
    logger.info(f"Extracted {len(changes)} change records")
    return changes


def main():
    """Main entry point."""
    logger.info("=" * 60)
    logger.info("Extract Seed Data from Excel")
    logger.info("=" * 60)
    
    # Find Excel file
    excel_file = Path(__file__).parent.parent / "data" / "IncidentRCAKnowledge.xlsx"
    if not excel_file.exists():
        logger.error(f"Excel file not found: {excel_file}")
        return 1
    
    try:
        # Extract incidents
        incidents = extract_incidents(excel_file)
        
        # Extract changes
        changes = extract_changes(excel_file)
        
        # Output to same data directory as Excel file
        data_dir = excel_file.parent
        
        # Save to JSON files
        incidents_file = data_dir / "historical_incidents.json"
        with open(incidents_file, "w", encoding="utf-8") as f:
            json.dump(incidents, f, indent=2, ensure_ascii=False)
        logger.info(f"✓ Saved incidents to: {incidents_file}")
        
        changes_file = data_dir / "change_records.json"
        with open(changes_file, "w", encoding="utf-8") as f:
            json.dump(changes, f, indent=2, ensure_ascii=False)
        logger.info(f"✓ Saved changes to: {changes_file}")
        
        logger.info(f"\n✓ Extraction complete!")
        logger.info(f"  - {len(incidents)} incidents")
        logger.info(f"  - {len(changes)} change records")
        
        return 0
        
    except Exception as e:
        logger.error(f"Error extracting data: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())

"""Repository pattern for Cosmos DB data access."""
from typing import List, Optional, Dict, Any
from azure.cosmos.exceptions import CosmosHttpResponseError
from src.cosmos.client import CosmosDBClient
from src.common.logging import get_logger
from src.common.exceptions import CosmosDBException

logger = get_logger(__name__)


class IncidentRepository:
    """Repository for historical incident data access."""
    
    def __init__(self, cosmos_client: CosmosDBClient):
        """
        Initialize the incident repository.
        
        Args:
            cosmos_client: Cosmos DB client instance
        """
        self.cosmos_client = cosmos_client
        self.container = cosmos_client.incident_container
    
    def get_by_id(self, incident_id: str, service_key: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve an incident by ID.
        
        Args:
            incident_id: The incident ID
            service_key: The partition key (serviceKey)
            
        Returns:
            Incident document or None if not found
        """
        try:
            item = self.container.read_item(
                item=incident_id,
                partition_key=service_key
            )
            logger.info(f"Retrieved incident: {incident_id}")
            return item
        except CosmosHttpResponseError as e:
            if e.status_code == 404:
                logger.warning(f"Incident not found: {incident_id}")
                return None
            raise CosmosDBException(f"Error retrieving incident: {e}")
    
    def query_by_service(
        self,
        service_key: str,
        is_resolved: bool = True,
        max_items: int = 25
    ) -> List[Dict[str, Any]]:
        """
        Query incidents by service key.
        
        Args:
            service_key: The service partition key
            is_resolved: Filter for resolved incidents only
            max_items: Maximum number of items to return
            
        Returns:
            List of incident documents
        """
        try:
            query = """
                SELECT * FROM c 
                WHERE c.serviceKey = @serviceKey 
                AND c.isResolved = @isResolved
                AND c.documentType = 'historicalIncident'
            """
            
            parameters = [
                {"name": "@serviceKey", "value": service_key},
                {"name": "@isResolved", "value": is_resolved}
            ]
            
            items = list(self.container.query_items(
                query=query,
                parameters=parameters,
                partition_key=service_key,
                max_item_count=max_items
            ))
            
            logger.info(f"Retrieved {len(items)} incidents for service: {service_key}")
            return items
            
        except CosmosHttpResponseError as e:
            raise CosmosDBException(f"Error querying incidents: {e}")
    
    def query_all_resolved(self, max_items: int = 50) -> List[Dict[str, Any]]:
        """
        Query all resolved incidents (cross-partition).
        Use sparingly - prefer querying by service key.
        
        Args:
            max_items: Maximum number of items to return
            
        Returns:
            List of incident documents
        """
        try:
            query = """
                SELECT * FROM c 
                WHERE c.isResolved = true
                AND c.documentType = 'historicalIncident'
            """
            
            items = list(self.container.query_items(
                query=query,
                enable_cross_partition_query=True,
                max_item_count=max_items
            ))
            
            logger.info(f"Retrieved {len(items)} resolved incidents (cross-partition)")
            return items
            
        except CosmosHttpResponseError as e:
            raise CosmosDBException(f"Error querying all incidents: {e}")

    def query_by_incident_id(self, incident_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve a single incident by incidentId (cross-partition).
        Used for direct lookups when the service key is not known.

        Args:
            incident_id: The incident ID (e.g. INC10004)

        Returns:
            Incident document or None if not found
        """
        try:
            query = "SELECT * FROM c WHERE c.incidentId = @incidentId AND c.documentType = 'historicalIncident'"
            parameters = [{"name": "@incidentId", "value": incident_id}]

            items = list(self.container.query_items(
                query=query,
                parameters=parameters,
                enable_cross_partition_query=True,
            ))

            if items:
                logger.info(f"Found incident by ID: {incident_id}")
                return items[0]

            logger.warning(f"Incident not found by ID: {incident_id}")
            return None

        except CosmosHttpResponseError as e:
            raise CosmosDBException(f"Error querying incident by ID: {e}")
    
    def create(self, incident: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new incident document.
        
        Args:
            incident: Incident document to create
            
        Returns:
            Created incident document
        """
        try:
            item = self.container.create_item(body=incident)
            logger.info(f"Created incident: {incident.get('id')}")
            return item
        except CosmosHttpResponseError as e:
            raise CosmosDBException(f"Error creating incident: {e}")


class ChangeRepository:
    """Repository for change record data access."""
    
    def __init__(self, cosmos_client: CosmosDBClient):
        """
        Initialize the change repository.
        
        Args:
            cosmos_client: Cosmos DB client instance
        """
        self.cosmos_client = cosmos_client
        self.container = cosmos_client.change_container
    
    def get_by_id(self, change_id: str, service_key: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve a change record by ID.
        
        Args:
            change_id: The change ID
            service_key: The partition key (serviceKey)
            
        Returns:
            Change document or None if not found
        """
        try:
            item = self.container.read_item(
                item=change_id,
                partition_key=service_key
            )
            logger.info(f"Retrieved change: {change_id}")
            return item
        except CosmosHttpResponseError as e:
            if e.status_code == 404:
                logger.warning(f"Change not found: {change_id}")
                return None
            raise CosmosDBException(f"Error retrieving change: {e}")
    
    def query_by_change_id(self, change_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve a single change record by changeId (cross-partition).
        Used for direct lookups when the service key is not known.

        Args:
            change_id: The change ID (e.g. CHG50004)

        Returns:
            Change document or None if not found
        """
        try:
            query = "SELECT * FROM c WHERE c.changeId = @changeId AND c.documentType = 'changeRecord'"
            parameters = [{"name": "@changeId", "value": change_id}]

            items = list(self.container.query_items(
                query=query,
                parameters=parameters,
                enable_cross_partition_query=True,
            ))

            if items:
                logger.info(f"Found change record by ID: {change_id}")
                return items[0]

            logger.warning(f"Change record not found by ID: {change_id}")
            return None

        except CosmosHttpResponseError as e:
            raise CosmosDBException(f"Error querying change by ID: {e}")

    def create(self, change: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new change record document.
        
        Args:
            change: Change document to create
            
        Returns:
            Created change document
        """
        try:
            item = self.container.create_item(body=change)
            logger.info(f"Created change: {change.get('id')}")
            return item
        except CosmosHttpResponseError as e:
            raise CosmosDBException(f"Error creating change: {e}")

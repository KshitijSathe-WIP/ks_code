"""Unit tests for Cosmos DB repositories."""
import pytest
from unittest.mock import Mock, MagicMock
from src.cosmos.repositories import IncidentRepository, ChangeRepository
from src.common.exceptions import CosmosDBException


@pytest.fixture
def mock_cosmos_client():
    """Create a mock Cosmos DB client."""
    client = Mock()
    client.incident_container = Mock()
    client.change_container = Mock()
    return client


@pytest.fixture
def incident_repository(mock_cosmos_client):
    """Create an IncidentRepository with mocked client."""
    return IncidentRepository(mock_cosmos_client)


@pytest.fixture
def change_repository(mock_cosmos_client):
    """Create a ChangeRepository with mocked client."""
    return ChangeRepository(mock_cosmos_client)


class TestIncidentRepository:
    """Tests for IncidentRepository."""
    
    def test_get_by_id_success(self, incident_repository, mock_cosmos_client):
        """Test successful incident retrieval by ID."""
        # Arrange
        expected_incident = {"id": "INC10001", "incidentId": "INC10001"}
        mock_cosmos_client.incident_container.read_item.return_value = expected_incident
        
        # Act
        result = incident_repository.get_by_id("INC10001", "mobile-banking")
        
        # Assert
        assert result == expected_incident
        mock_cosmos_client.incident_container.read_item.assert_called_once_with(
            item="INC10001",
            partition_key="mobile-banking"
        )
    
    def test_get_by_id_not_found(self, incident_repository, mock_cosmos_client):
        """Test incident retrieval when not found."""
        # Arrange
        from azure.cosmos.exceptions import CosmosHttpResponseError
        error = CosmosHttpResponseError(status_code=404, message="Not found")
        mock_cosmos_client.incident_container.read_item.side_effect = error
        
        # Act
        result = incident_repository.get_by_id("INC99999", "mobile-banking")
        
        # Assert
        assert result is None
    
    def test_query_by_service(self, incident_repository, mock_cosmos_client):
        """Test querying incidents by service key."""
        # Arrange
        expected_incidents = [
            {"id": "INC10001", "serviceKey": "mobile-banking"},
            {"id": "INC10002", "serviceKey": "mobile-banking"}
        ]
        mock_cosmos_client.incident_container.query_items.return_value = expected_incidents
        
        # Act
        result = incident_repository.query_by_service("mobile-banking")
        
        # Assert
        assert result == expected_incidents
        assert mock_cosmos_client.incident_container.query_items.called
    
    def test_create_incident(self, incident_repository, mock_cosmos_client):
        """Test creating a new incident."""
        # Arrange
        new_incident = {
            "id": "INC10001",
            "serviceKey": "mobile-banking",
            "incidentTitle": "Test Incident"
        }
        mock_cosmos_client.incident_container.create_item.return_value = new_incident
        
        # Act
        result = incident_repository.create(new_incident)
        
        # Assert
        assert result == new_incident
        mock_cosmos_client.incident_container.create_item.assert_called_once_with(body=new_incident)


class TestChangeRepository:
    """Tests for ChangeRepository."""
    
    def test_get_by_id_success(self, change_repository, mock_cosmos_client):
        """Test successful change retrieval by ID."""
        # Arrange
        expected_change = {"id": "CHG50001", "changeId": "CHG50001"}
        mock_cosmos_client.change_container.read_item.return_value = expected_change
        
        # Act
        result = change_repository.get_by_id("CHG50001", "mobile-banking")
        
        # Assert
        assert result == expected_change
        mock_cosmos_client.change_container.read_item.assert_called_once_with(
            item="CHG50001",
            partition_key="mobile-banking"
        )
    
    def test_get_by_id_not_found(self, change_repository, mock_cosmos_client):
        """Test change retrieval when not found."""
        # Arrange
        from azure.cosmos.exceptions import CosmosHttpResponseError
        error = CosmosHttpResponseError(status_code=404, message="Not found")
        mock_cosmos_client.change_container.read_item.side_effect = error
        
        # Act
        result = change_repository.get_by_id("CHG99999", "mobile-banking")
        
        # Assert
        assert result is None
    
    def test_create_change(self, change_repository, mock_cosmos_client):
        """Test creating a new change record."""
        # Arrange
        new_change = {
            "id": "CHG50001",
            "serviceKey": "mobile-banking",
            "changeTitle": "Test Change"
        }
        mock_cosmos_client.change_container.create_item.return_value = new_change
        
        # Act
        result = change_repository.create(new_change)
        
        # Assert
        assert result == new_change
        mock_cosmos_client.change_container.create_item.assert_called_once_with(body=new_change)

"""Basic health check test for the API."""
import pytest
from fastapi.testclient import TestClient
from src.api.main import app

client = TestClient(app)


def test_health_endpoint():
    """Test that health endpoint returns expected structure."""
    response = client.get("/health")
    assert response.status_code == 200
    
    data = response.json()
    assert "status" in data
    assert "version" in data
    assert "cosmos_connected" in data
    assert "foundry_configured" in data
    assert data["version"] == "0.1.0"


def test_root_endpoint():
    """Test that root endpoint returns API information."""
    response = client.get("/")
    assert response.status_code == 200
    
    data = response.json()
    assert "name" in data
    assert "version" in data
    assert data["version"] == "0.1.0"


def test_evidence_endpoint_accepts_request():
    """Test that evidence endpoint accepts valid requests."""
    response = client.post(
        "/api/rca/evidence",
        json={
            "incident_description": "Mobile banking app not working",
            "top_incident_count": 3
        }
    )
    assert response.status_code == 200
    
    data = response.json()
    assert "interpreted_context" in data
    assert "historical_matches" in data
    assert "related_changes" in data
    assert "request_id" in data
    assert "timestamp" in data


def test_evidence_endpoint_validates_input():
    """Test that evidence endpoint validates request input."""
    # Too short description
    response = client.post(
        "/api/rca/evidence",
        json={
            "incident_description": "bad",
            "top_incident_count": 3
        }
    )
    assert response.status_code == 422  # Validation error

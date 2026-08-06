"""Pytest fixtures shared across the test suite."""
import os
import pytest


@pytest.fixture(autouse=True)
def set_env_vars(monkeypatch):
    """Provide minimal env vars so modules load without real credentials."""
    monkeypatch.setenv("AZURE_TENANT_ID", "test-tenant")
    monkeypatch.setenv("AZURE_CLIENT_ID", "test-client")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("DATAVERSE_URL", "https://test.crm.dynamics.com")
    monkeypatch.setenv("APPINSIGHTS_INSTRUMENTATIONKEY", "")
    monkeypatch.setenv("PMO_TEAMS_WEBHOOK_URL", "")
    monkeypatch.setenv("SHADOW_MODE", "true")

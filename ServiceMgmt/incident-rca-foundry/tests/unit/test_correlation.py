"""Unit tests for change correlation logic."""
import pytest
from src.retrieval.correlation import ChangeCorrelator


@pytest.fixture
def correlator():
    """Create a change correlator instance."""
    return ChangeCorrelator()


@pytest.fixture
def sample_incident():
    """Create a sample incident."""
    return {
        "incidentId": "INC10001",
        "serviceKey": "mobile-banking",
        "businessService": "Mobile Banking",
        "applicationName": "Mobile Banking API",
        "configurationItem": "MB-API-PROD-01",
        "rootCauseCategory": "Application",
        "linkedChangeId": "CHG50001"
    }


@pytest.fixture
def sample_change_supported():
    """Create a sample change with supporting evidence."""
    return {
        "changeId": "CHG50001",
        "serviceKey": "mobile-banking",
        "businessService": "Mobile Banking",
        "applicationName": "OAuth Service",
        "configurationItem": "MB-API-PROD-01",
        "changeCategory": "Application",
        "validationResult": "Partially Successful",
        "rollbackPerformed": False,
        "postImplementationIssues": [
            "Connection timeouts observed",
            "High CPU usage"
        ],
        "changeCorrelationNotes": "Incident occurred 2 hours after deployment"
    }


@pytest.fixture
def sample_change_unsupported():
    """Create a sample change without supporting evidence."""
    return {
        "changeId": "CHG50002",
        "serviceKey": "online-banking",  # Different service!
        "businessService": "Online Banking",
        "applicationName": "Web Portal",
        "configurationItem": "WEB-PORTAL-01",
        "changeCategory": "Infrastructure",
        "validationResult": "Successful",
        "rollbackPerformed": False,
        "postImplementationIssues": [],
        "changeCorrelationNotes": ""
    }


class TestChangeCorrelator:
    """Tests for ChangeCorrelator."""
    
    def test_validate_service_alignment_matching_service_key(self, correlator, sample_incident, sample_change_supported):
        """Test service alignment with matching service keys."""
        aligned = correlator.validate_service_alignment(sample_incident, sample_change_supported)
        assert aligned is True
    
    def test_validate_service_alignment_no_match(self, correlator, sample_incident, sample_change_unsupported):
        """Test service alignment with different services."""
        aligned = correlator.validate_service_alignment(sample_incident, sample_change_unsupported)
        assert aligned is False
    
    def test_validate_root_cause_support_with_issues(self, correlator, sample_incident, sample_change_supported):
        """Test root cause support validation with post-implementation issues."""
        is_supported, reasons = correlator.validate_root_cause_support(sample_incident, sample_change_supported)
        
        assert is_supported is True
        assert len(reasons) > 0
        assert any("category" in r.lower() for r in reasons)
        assert any("issue" in r.lower() for r in reasons)
    
    def test_validate_root_cause_support_no_evidence(self, correlator, sample_incident):
        """Test root cause support with no supporting evidence."""
        clean_change = {
            "changeId": "CHG50003",
            "changeCategory": "Application",
            "validationResult": "Successful",
            "rollbackPerformed": False,
            "postImplementationIssues": [],
            "changeCorrelationNotes": ""
        }
        
        is_supported, reasons = correlator.validate_root_cause_support(sample_incident, clean_change)
        
        # Should have some support from matching category
        assert len(reasons) >= 1
    
    def test_correlate_change_supported(self, correlator, sample_incident, sample_change_supported):
        """Test full correlation with supported change."""
        result = correlator.correlate_change(sample_incident, sample_change_supported)
        
        assert result is not None
        assert result.change_id == "CHG50001"
        assert result.change_supported is True
        assert len(result.reasons) > 0
    
    def test_correlate_change_unsupported_service_mismatch(self, correlator, sample_incident, sample_change_unsupported):
        """Test full correlation with unsupported change (service mismatch)."""
        result = correlator.correlate_change(sample_incident, sample_change_unsupported)
        
        assert result is not None
        assert result.change_id == "CHG50002"
        assert result.change_supported is False
        assert any("not align" in r.lower() for r in result.reasons)
    
    def test_correlate_change_none(self, correlator, sample_incident):
        """Test correlation with no change provided."""
        result = correlator.correlate_change(sample_incident, None)
        assert result is None
    
    def test_rollback_indication(self, correlator, sample_incident):
        """Test that rollback performed is indicated in reasons."""
        change_with_rollback = {
            "changeId": "CHG50004",
            "serviceKey": "mobile-banking",
            "businessService": "Mobile Banking",
            "changeCategory": "Application",
            "validationResult": "Failed",
            "rollbackPerformed": True,
            "postImplementationIssues": ["Critical failure"],
            "changeCorrelationNotes": ""
        }
        
        result = correlator.correlate_change(sample_incident, change_with_rollback)
        
        assert result is not None
        assert result.change_supported is True
        
        # Check that rollback is mentioned in the reasons
        assert "Change was rolled back" in result.reasons

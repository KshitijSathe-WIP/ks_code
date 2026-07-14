"""Unit tests for RCA response validator."""
import pytest
from src.foundry.validator import RCAResponseValidator
from src.common.exceptions import ValidationException


@pytest.fixture
def validator():
    """Create a validator instance."""
    return RCAResponseValidator()


@pytest.fixture
def valid_response():
    """Create a valid RCA response."""
    return {
        "rootCause": "Load balancer health-check misconfiguration kept routing traffic to a degraded API node",
        "rootCauseCategory": "Network",
        "confidence": 82,
        "matchedIncidentIds": ["INC10014"],
        "relatedChangeId": "CHG50014",
        "changeCorrelation": True,
        "evidence": [
            "Similarity score: 72/100",
            "Historical incident: INC10014",
            "Matched service: Mobile Banking",
            "Symptom match: intermittent latency, overloaded node",
            "Root cause category: Network",
            "Related change: CHG50014 - Load Balancer Health Check Update",
            "Change validation: Partially Successful",
            "Post-implementation issues: 2 reported"
        ]
    }


class TestRCAResponseValidator:
    """Test suite for RCA response validator."""
    
    def test_valid_response(self, validator, valid_response):
        """Test validation of a valid response."""
        is_valid, errors = validator.validate(valid_response)
        assert is_valid is True
        assert len(errors) == 0
    
    def test_valid_response_no_change(self, validator):
        """Test valid response without change correlation."""
        response = {
            "rootCause": "Database connection pool exhausted under high load",
            "rootCauseCategory": "Database",
            "confidence": 75,
            "matchedIncidentIds": ["INC10001", "INC10002"],
            "relatedChangeId": "",
            "changeCorrelation": False,
            "evidence": [
                "Similarity score: 68/100",
                "Historical incident: INC10001",
                "Historical incident: INC10002",
                "Matched service: Payments Platform"
            ]
        }
        
        is_valid, errors = validator.validate(response)
        assert is_valid is True
        assert len(errors) == 0
    
    def test_zero_confidence_response(self, validator):
        """Test valid response with zero confidence."""
        response = {
            "rootCause": "No similar historical incidents found",
            "rootCauseCategory": "Unknown",
            "confidence": 0,
            "matchedIncidentIds": [],
            "relatedChangeId": "",
            "changeCorrelation": False,
            "evidence": [
                "No grounded evidence available"
            ]
        }
        
        is_valid, errors = validator.validate(response)
        assert is_valid is True
        assert len(errors) == 0
    
    def test_missing_required_field(self, validator, valid_response):
        """Test validation fails for missing required field."""
        del valid_response["rootCause"]
        
        is_valid, errors = validator.validate(valid_response)
        assert is_valid is False
        assert len(errors) > 0
        assert any("rootCause" in str(e) for e in errors)
    
    def test_invalid_confidence_range(self, validator, valid_response):
        """Test validation fails for out-of-range confidence."""
        valid_response["confidence"] = 150
        
        is_valid, errors = validator.validate(valid_response)
        assert is_valid is False
        assert len(errors) > 0
    
    def test_invalid_root_cause_category(self, validator, valid_response):
        """Test validation fails for invalid category."""
        valid_response["rootCauseCategory"] = "InvalidCategory"
        
        is_valid, errors = validator.validate(valid_response)
        assert is_valid is False
        assert len(errors) > 0
    
    def test_invalid_incident_id_format(self, validator, valid_response):
        """Test validation fails for invalid incident ID format."""
        valid_response["matchedIncidentIds"] = ["INVALID_ID"]
        
        is_valid, errors = validator.validate(valid_response)
        assert is_valid is False
        assert len(errors) > 0
    
    def test_invalid_change_id_format(self, validator, valid_response):
        """Test validation fails for invalid change ID format."""
        valid_response["relatedChangeId"] = "INVALID_CHG"
        
        is_valid, errors = validator.validate(valid_response)
        assert is_valid is False
        assert len(errors) > 0
    
    def test_change_correlation_without_change_id(self, validator, valid_response):
        """Test business rule: changeCorrelation true requires relatedChangeId."""
        valid_response["changeCorrelation"] = True
        valid_response["relatedChangeId"] = ""
        
        is_valid, errors = validator.validate(valid_response)
        assert is_valid is False
        assert any("changeCorrelation is true but relatedChangeId is empty" in e for e in errors)
    
    def test_zero_confidence_with_incidents(self, validator, valid_response):
        """Test business rule: confidence 0 should have empty matchedIncidentIds."""
        valid_response["confidence"] = 0
        valid_response["matchedIncidentIds"] = ["INC10001"]
        
        is_valid, errors = validator.validate(valid_response)
        assert is_valid is False
        assert any("Confidence is 0 but matchedIncidentIds is not empty" in e for e in errors)
    
    def test_change_id_not_in_evidence(self, validator, valid_response):
        """Test business rule: relatedChangeId should be mentioned in evidence."""
        valid_response["relatedChangeId"] = "CHG50099"
        valid_response["evidence"] = [
            "Similarity score: 72/100",
            "Historical incident: INC10014"
        ]
        
        is_valid, errors = validator.validate(valid_response)
        assert is_valid is False
        assert any("CHG50099" in e and "not mentioned in evidence" in e for e in errors)
    
    def test_incident_id_not_in_evidence(self, validator, valid_response):
        """Test business rule: matchedIncidentIds should be mentioned in evidence."""
        valid_response["matchedIncidentIds"] = ["INC10001", "INC10002"]
        valid_response["evidence"] = [
            "Historical incident: INC10001"
            # INC10002 is missing
        ]
        
        is_valid, errors = validator.validate(valid_response)
        assert is_valid is False
        assert any("INC10002" in e and "not mentioned in evidence" in e for e in errors)
    
    def test_empty_evidence_array(self, validator, valid_response):
        """Test business rule: evidence array should not be empty."""
        valid_response["evidence"] = []
        
        is_valid, errors = validator.validate(valid_response)
        assert is_valid is False
        # Schema validation catches this first
        assert any("non-empty" in e.lower() or "evidence" in e.lower() for e in errors)
    
    def test_high_confidence_with_vague_root_cause(self, validator, valid_response):
        """Test business rule: high confidence should not have vague root cause."""
        valid_response["confidence"] = 90
        valid_response["rootCause"] = "Possibly a network issue, uncertain"
        
        is_valid, errors = validator.validate(valid_response)
        assert is_valid is False
        assert any("vague terms" in e for e in errors)
    
    def test_validate_or_raise_success(self, validator, valid_response):
        """Test validate_or_raise does not raise for valid response."""
        validator.validate_or_raise(valid_response)
        # Should not raise
    
    def test_validate_or_raise_failure(self, validator, valid_response):
        """Test validate_or_raise raises ValidationException for invalid response."""
        valid_response["confidence"] = 150
        
        with pytest.raises(ValidationException) as exc_info:
            validator.validate_or_raise(valid_response)
        
        assert "validation failed" in str(exc_info.value).lower()
    
    def test_additional_properties_not_allowed(self, validator, valid_response):
        """Test validation fails when additional properties are present."""
        valid_response["extraField"] = "should not be here"
        
        is_valid, errors = validator.validate(valid_response)
        assert is_valid is False
        assert len(errors) > 0
    
    def test_max_matched_incidents(self, validator, valid_response):
        """Test validation enforces max 3 matched incidents."""
        valid_response["matchedIncidentIds"] = [
            "INC10001", "INC10002", "INC10003", "INC10004"
        ]
        valid_response["evidence"] = [
            "Historical incident: INC10001",
            "Historical incident: INC10002",
            "Historical incident: INC10003",
            "Historical incident: INC10004"
        ]
        
        is_valid, errors = validator.validate(valid_response)
        assert is_valid is False
        assert len(errors) > 0

"""Unit tests for deterministic scorer."""
import pytest
from src.retrieval.normalizer import InterpretedContext
from src.retrieval.scorer import DeterministicScorer, ScoreBreakdown


@pytest.fixture
def scorer():
    """Create a scorer instance."""
    return DeterministicScorer(min_score_threshold=30)


@pytest.fixture
def mobile_banking_context():
    """Create a mobile banking context."""
    return InterpretedContext(
        business_service="Mobile Banking",
        service_key="mobile-banking",
        probable_application=None,
        symptoms=["unavailable", "failed"],
        keywords=["mobile", "banking", "app", "not", "working"]
    )


@pytest.fixture
def sample_incident():
    """Create a sample incident document."""
    return {
        "id": "INC10001",
        "incidentId": "INC10001",
        "serviceKey": "mobile-banking",
        "businessService": "Mobile Banking",
        "applicationName": "Mobile Banking API",
        "incidentTitle": "Mobile Banking Login Failures",
        "symptoms": ["unavailable", "failed", "authentication_error"],
        "tags": ["mobile_banking", "authentication", "login"],
        "errorCodes": ["AUTH-401"],
        "configurationItem": "MB-API-PROD-01",
        "searchText": "mobile banking mobile banking api login failure authentication unavailable",
        "rootCause": "OAuth service degradation",
        "rootCauseCategory": "Application"
    }


class TestDeterministicScorer:
    """Tests for DeterministicScorer."""
    
    def test_calculate_service_score_exact_match(self, scorer, mobile_banking_context, sample_incident):
        """Test service score with exact match."""
        score = scorer.calculate_service_score(mobile_banking_context, sample_incident)
        assert score == 25  # Full weight
    
    def test_calculate_service_score_no_match(self, scorer, mobile_banking_context):
        """Test service score with no match."""
        incident = {
            "serviceKey": "online-banking",
            "businessService": "Online Banking"
        }
        score = scorer.calculate_service_score(mobile_banking_context, incident)
        assert score == 0
    
    def test_calculate_symptom_score_full_overlap(self, scorer, mobile_banking_context, sample_incident):
        """Test symptom score with full overlap."""
        # Context has ["unavailable", "failed"]
        # Incident has ["unavailable", "failed", "authentication_error"]
        score = scorer.calculate_symptom_score(mobile_banking_context, sample_incident)
        assert score == 25  # Full match of context symptoms
    
    def test_calculate_symptom_score_partial_overlap(self, scorer, mobile_banking_context):
        """Test symptom score with partial overlap."""
        incident = {
            "symptoms": ["unavailable", "timeout"]  # Only 1 of 2 matches
        }
        score = scorer.calculate_symptom_score(mobile_banking_context, incident)
        assert 0 < score < 25  # Proportional score
    
    def test_calculate_tag_score(self, scorer, mobile_banking_context, sample_incident):
        """Test tag/keyword overlap score."""
        # Context keywords: ["mobile", "banking", "app", "not", "working"]
        # All should match in searchText
        score = scorer.calculate_tag_score(mobile_banking_context, sample_incident)
        assert score > 0  # Some overlap expected
    
    def test_score_incident_full(self, scorer, mobile_banking_context, sample_incident):
        """Test full incident scoring."""
        scored = scorer.score_incident(mobile_banking_context, sample_incident)
        
        assert scored.incident == sample_incident
        assert scored.score > 0
        assert scored.breakdown.service == 25  # Exact service match
        assert scored.breakdown.symptoms == 25  # Full symptom overlap
        assert scored.breakdown.total == scored.score
    
    def test_score_and_rank(self, scorer, mobile_banking_context):
        """Test scoring and ranking multiple incidents."""
        incidents = [
            {
                "id": "INC10001",
                "incidentId": "INC10001",
                "serviceKey": "mobile-banking",
                "businessService": "Mobile Banking",
                "symptoms": ["unavailable", "failed"],
                "tags": [],
                "searchText": "mobile banking unavailable"
            },
            {
                "id": "INC10002",
                "incidentId": "INC10002",
                "serviceKey": "online-banking",
                "businessService": "Online Banking",
                "symptoms": ["slow"],
                "tags": [],
                "searchText": "online banking slow"
            },
            {
                "id": "INC10003",
                "incidentId": "INC10003",
                "serviceKey": "mobile-banking",
                "businessService": "Mobile Banking",
                "symptoms": ["timeout"],
                "tags": [],
                "searchText": "mobile banking timeout"
            }
        ]
        
        ranked = scorer.score_and_rank(mobile_banking_context, incidents, top_k=2)
        
        # Should return top 2, sorted by score
        assert len(ranked) <= 2
        assert ranked[0].score >= ranked[1].score if len(ranked) > 1 else True
        
        # Mobile banking incidents should score higher
        assert ranked[0].incident.get("serviceKey") == "mobile-banking"
    
    def test_min_score_threshold(self, scorer, mobile_banking_context):
        """Test that incidents below threshold are filtered."""
        poor_incident = {
            "id": "INC99999",
            "incidentId": "INC99999",
            "serviceKey": "unknown",
            "businessService": "Unknown Service",
            "symptoms": [],
            "tags": [],
            "searchText": "completely unrelated incident"
        }
        
        ranked = scorer.score_and_rank(mobile_banking_context, [poor_incident], top_k=1)
        
        # Should be filtered out if below threshold (30)
        if len(ranked) > 0:
            assert ranked[0].score >= 30

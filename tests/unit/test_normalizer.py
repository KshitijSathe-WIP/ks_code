"""Unit tests for natural language normalizer."""
import pytest
from src.retrieval.normalizer import NaturalLanguageNormalizer


@pytest.fixture
def normalizer():
    """Create a normalizer instance."""
    return NaturalLanguageNormalizer()


class TestNaturalLanguageNormalizer:
    """Tests for NaturalLanguageNormalizer."""
    
    def test_normalize_text(self, normalizer):
        """Test text normalization."""
        result = normalizer.normalize_text("  Mobile  Banking   APP  ")
        assert result == "mobile banking app"
    
    def test_extract_keywords(self, normalizer):
        """Test keyword extraction."""
        text = "The mobile banking app is not working and users cannot login"
        keywords = normalizer.extract_keywords(text)
        
        assert "mobile" in keywords
        assert "banking" in keywords
        assert "app" in keywords
        assert "working" in keywords
        assert "users" in keywords
        assert "login" in keywords
        
        # Stop words should be removed
        assert "the" not in keywords
        assert "is" not in keywords
        assert "and" not in keywords
    
    def test_infer_service_mobile_banking(self, normalizer):
        """Test service inference for mobile banking."""
        text = "Mobile banking app not working"
        service_info = normalizer.infer_service(text)
        
        assert service_info is not None
        business_service, service_key = service_info
        assert service_key == "mobile-banking"
        assert "Mobile" in business_service or "Banking" in business_service
    
    def test_infer_service_online_banking(self, normalizer):
        """Test service inference for online banking."""
        text = "Users cannot access online banking"
        service_info = normalizer.infer_service(text)
        
        assert service_info is not None
        _, service_key = service_info
        assert service_key == "online-banking"
    
    def test_infer_service_payments(self, normalizer):
        """Test service inference for payments."""
        text = "Payment platform experiencing issues"
        service_info = normalizer.infer_service(text)
        
        assert service_info is not None
        _, service_key = service_info
        assert service_key == "payments-platform"
    
    def test_infer_service_none(self, normalizer):
        """Test service inference when no service is mentioned."""
        text = "Something is broken"
        service_info = normalizer.infer_service(text)
        
        assert service_info is None
    
    def test_infer_symptoms_unavailable(self, normalizer):
        """Test symptom inference for unavailable/down."""
        text = "The service is not working and completely down"
        symptoms = normalizer.infer_symptoms(text)
        
        assert "unavailable" in symptoms or "down" in symptoms or "failed" in symptoms
    
    def test_infer_symptoms_slow(self, normalizer):
        """Test symptom inference for slowness."""
        text = "The application is very slow with high latency"
        symptoms = normalizer.infer_symptoms(text)
        
        assert any(s in symptoms for s in ["slow", "latency", "timeout", "degradation"])
    
    def test_infer_symptoms_login(self, normalizer):
        """Test symptom inference for login issues."""
        text = "Users cannot log in to the system"
        symptoms = normalizer.infer_symptoms(text)
        
        assert any(s in symptoms for s in ["login_failure", "authentication_failure", "auth_error"])
    
    def test_interpret_full_context(self, normalizer):
        """Test full context interpretation."""
        text = "Mobile banking app not working"
        context = normalizer.interpret(text)
        
        assert context.service_key == "mobile-banking"
        assert len(context.keywords) > 0
        assert "mobile" in context.keywords or "banking" in context.keywords
        assert len(context.symptoms) > 0

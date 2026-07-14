"""Natural language input normalization and interpretation."""
from typing import List, Optional, Set
import re
from dataclasses import dataclass
from src.common.logging import get_logger

logger = get_logger(__name__)


# Synonym dictionaries for service and symptom mapping
SERVICE_SYNONYMS = {
    "mobile": ["mobile-banking"],
    "mobile banking": ["mobile-banking"],
    "mobile bank": ["mobile-banking"],
    "mobile app": ["mobile-banking"],
    
    "online": ["online-banking"],
    "online banking": ["online-banking"],
    "online bank": ["online-banking"],
    "web banking": ["online-banking"],
    "internet banking": ["online-banking"],
    
    "payment": ["payments-platform"],
    "payments": ["payments-platform"],
    "payment platform": ["payments-platform"],
    "pay": ["payments-platform"],
    
    "regulatory": ["regulatory-reporting-platform"],
    "regulatory reporting": ["regulatory-reporting-platform"],
    "compliance": ["regulatory-reporting-platform"],
    "reporting": ["regulatory-reporting-platform"],
    
    "fraud": ["fraud-detection-platform"],
    "fraud detection": ["fraud-detection-platform"],
    
    "customer profile": ["customer-profile-service"],
    "profile": ["customer-profile-service"],
    
    "credit card": ["credit-card-processing"],
    "card": ["credit-card-processing"],
    
    "data warehouse": ["data-warehouse-platform"],
    "warehouse": ["data-warehouse-platform"],
    "dwh": ["data-warehouse-platform"],
    
    "mortgage": ["mortgage-platform"],
    "loan": ["mortgage-platform"],
    
    "treasury": ["treasury-platform"],
    
    "wire": ["wire-transfer-system"],
    "wire transfer": ["wire-transfer-system"],
}


SYMPTOM_SYNONYMS = {
    "not working": ["unavailable", "failed", "outage", "error", "down"],
    "down": ["unavailable", "outage", "failed", "offline"],
    "unavailable": ["down", "offline", "failed", "outage"],
    "failed": ["error", "failure", "unsuccessful", "broken"],
    "error": ["failed", "failure", "exception", "issue"],
    
    "slow": ["latency", "timeout", "degradation", "performance", "delay"],
    "slowness": ["latency", "timeout", "degradation", "performance"],
    "timeout": ["latency", "slow", "delay", "unresponsive"],
    "latency": ["slow", "delay", "performance", "timeout"],
    
    "cannot log in": ["login_failure", "authentication_failure", "auth_error"],
    "can't log in": ["login_failure", "authentication_failure", "auth_error"],
    "login fails": ["login_failure", "authentication_failure", "auth_error"],
    "login failure": ["authentication_failure", "auth_error", "access_denied"],
    "authentication": ["login_failure", "auth_error", "access_denied"],
    
    "crash": ["failure", "outage", "exception", "error"],
    "crashing": ["failure", "outage", "exception", "error"],
}


@dataclass
class InterpretedContext:
    """Represents interpreted context from natural language input."""
    business_service: Optional[str] = None
    service_key: Optional[str] = None
    probable_application: Optional[str] = None
    symptoms: List[str] = None
    keywords: List[str] = None
    
    def __post_init__(self):
        if self.symptoms is None:
            self.symptoms = []
        if self.keywords is None:
            self.keywords = []


class NaturalLanguageNormalizer:
    """Normalizes natural language incident descriptions."""
    
    def __init__(self):
        """Initialize the normalizer."""
        self.service_synonyms = SERVICE_SYNONYMS
        self.symptom_synonyms = SYMPTOM_SYNONYMS
    
    def normalize_text(self, text: str) -> str:
        """
        Normalize text: lowercase, trim, remove extra spaces.
        
        Args:
            text: Input text
            
        Returns:
            Normalized text
        """
        if not text:
            return ""
        
        # Lowercase
        text = text.lower().strip()
        
        # Remove extra whitespace
        text = re.sub(r'\s+', ' ', text)
        
        return text
    
    def extract_keywords(self, text: str) -> List[str]:
        """
        Extract meaningful keywords from text.
        
        Args:
            text: Input text
            
        Returns:
            List of keywords
        """
        normalized = self.normalize_text(text)
        
        # Split into words
        words = normalized.split()
        
        # Remove common stop words
        stop_words = {
            "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", 
            "for", "of", "with", "by", "from", "is", "was", "are", "were",
            "be", "been", "being", "have", "has", "had", "do", "does", "did",
            "will", "would", "could", "should", "may", "might", "must",
            "this", "that", "these", "those", "i", "you", "he", "she", "it",
            "we", "they", "my", "your", "his", "her", "its", "our", "their"
        }
        
        keywords = [w for w in words if w not in stop_words and len(w) > 2]
        
        return keywords
    
    def infer_service(self, text: str) -> Optional[tuple[str, str]]:
        """
        Infer business service and service key from text.
        
        Args:
            text: Input text
            
        Returns:
            Tuple of (business_service_display_name, service_key) or None
        """
        normalized = self.normalize_text(text)
        
        # Check for service synonyms
        for synonym, service_keys in self.service_synonyms.items():
            if synonym in normalized:
                service_key = service_keys[0]  # Take first match
                
                # Convert service key back to display name
                display_name = " ".join(service_key.split("-")).title()
                
                logger.info(f"Inferred service: {display_name} ({service_key})")
                return display_name, service_key
        
        return None
    
    def infer_symptoms(self, text: str) -> List[str]:
        """
        Infer symptoms from text using synonym dictionary.
        
        Args:
            text: Input text
            
        Returns:
            List of symptom keywords
        """
        normalized = self.normalize_text(text)
        symptoms: Set[str] = set()
        
        # Check for symptom synonyms
        for trigger, symptom_list in self.symptom_synonyms.items():
            if trigger in normalized:
                symptoms.update(symptom_list)
                logger.debug(f"Detected symptom trigger: {trigger} -> {symptom_list}")
        
        return list(symptoms)
    
    def interpret(self, incident_description: str) -> InterpretedContext:
        """
        Interpret natural language incident description.
        
        Args:
            incident_description: User's incident description
            
        Returns:
            Interpreted context with service, symptoms, and keywords
        """
        logger.info(f"Interpreting: {incident_description}")
        
        # Infer service
        service_info = self.infer_service(incident_description)
        business_service = service_info[0] if service_info else None
        service_key = service_info[1] if service_info else None
        
        # Infer symptoms
        symptoms = self.infer_symptoms(incident_description)
        
        # Extract keywords
        keywords = self.extract_keywords(incident_description)
        
        context = InterpretedContext(
            business_service=business_service,
            service_key=service_key,
            probable_application=None,  # Could be inferred with more logic
            symptoms=symptoms,
            keywords=keywords
        )
        
        logger.info(f"Interpreted context: service={service_key}, symptoms={len(symptoms)}, keywords={len(keywords)}")
        return context

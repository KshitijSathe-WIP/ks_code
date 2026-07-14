"""Deterministic scoring algorithm for incident matching."""
from typing import List, Dict, Any, Set
from dataclasses import dataclass
from src.retrieval.normalizer import InterpretedContext
from src.common.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ScoreBreakdown:
    """Detailed breakdown of scoring components."""
    service: int = 0
    application: int = 0
    symptoms: int = 0
    tags: int = 0
    error_code: int = 0
    configuration_item: int = 0
    
    @property
    def total(self) -> int:
        """Calculate total score."""
        return (
            self.service +
            self.application +
            self.symptoms +
            self.tags +
            self.error_code +
            self.configuration_item
        )


@dataclass
class ScoredIncident:
    """An incident with its similarity score."""
    incident: Dict[str, Any]
    score: int
    breakdown: ScoreBreakdown
    
    @property
    def incident_id(self) -> str:
        """Get incident ID."""
        return self.incident.get("incidentId", "")
    
    @property
    def incident_title(self) -> str:
        """Get incident title."""
        return self.incident.get("incidentTitle", "")
    
    @property
    def root_cause(self) -> str:
        """Get root cause."""
        return self.incident.get("rootCause", "")
    
    @property
    def root_cause_category(self) -> str:
        """Get root cause category."""
        return self.incident.get("rootCauseCategory", "")
    
    @property
    def linked_change_id(self) -> str:
        """Get linked change ID."""
        return self.incident.get("linkedChangeId", "")


class DeterministicScorer:
    """
    Transparent weighted scoring for incident matching.
    
    Scoring weights:
    - Same BusinessService or serviceKey: +25
    - Same or close ApplicationName: +20
    - Symptom keyword overlap: +25
    - Tag or searchText overlap: +15
    - Matching error code: +10
    - Matching ConfigurationItem: +5
    Maximum: 100
    """
    
    WEIGHTS = {
        "service": 25,
        "application": 20,
        "symptoms": 25,
        "tags": 15,
        "error_code": 10,
        "configuration_item": 5
    }
    
    def __init__(self, min_score_threshold: int = 30):
        """
        Initialize the scorer.
        
        Args:
            min_score_threshold: Minimum score to consider a match meaningful
        """
        self.min_score_threshold = min_score_threshold
    
    def normalize_text(self, text: str) -> str:
        """Normalize text for comparison."""
        if not text:
            return ""
        return text.lower().strip().replace("_", "-").replace(" ", "-")
    
    def calculate_service_score(
        self,
        context: InterpretedContext,
        incident: Dict[str, Any]
    ) -> int:
        """
        Calculate service match score.
        
        Args:
            context: Interpreted context from user input
            incident: Candidate incident
            
        Returns:
            Service match score (0 or max weight)
        """
        if not context.service_key:
            return 0
        
        incident_service_key = incident.get("serviceKey", "")
        incident_business_service = self.normalize_text(incident.get("businessService", ""))
        context_business_service = self.normalize_text(context.business_service or "")
        
        # Exact service key match
        if context.service_key == incident_service_key:
            return self.WEIGHTS["service"]
        
        # Business service name match
        if context_business_service and context_business_service in incident_business_service:
            return self.WEIGHTS["service"]
        
        return 0
    
    def calculate_application_score(
        self,
        context: InterpretedContext,
        incident: Dict[str, Any]
    ) -> int:
        """
        Calculate application match score.
        
        Args:
            context: Interpreted context
            incident: Candidate incident
            
        Returns:
            Application match score
        """
        if not context.probable_application:
            return 0
        
        incident_app = self.normalize_text(incident.get("applicationName", ""))
        context_app = self.normalize_text(context.probable_application)
        
        if context_app and context_app in incident_app:
            return self.WEIGHTS["application"]
        
        return 0
    
    def calculate_symptom_score(
        self,
        context: InterpretedContext,
        incident: Dict[str, Any]
    ) -> int:
        """
        Calculate symptom overlap score.
        
        Args:
            context: Interpreted context
            incident: Candidate incident
            
        Returns:
            Symptom overlap score (proportional to overlap)
        """
        if not context.symptoms:
            return 0
        
        # Get incident symptoms
        incident_symptoms = incident.get("symptoms", [])
        incident_symptoms_set = {self.normalize_text(s) for s in incident_symptoms if s}
        
        # Get context symptoms
        context_symptoms_set = {self.normalize_text(s) for s in context.symptoms if s}
        
        if not incident_symptoms_set:
            return 0
        
        # Calculate overlap
        overlap = len(context_symptoms_set & incident_symptoms_set)
        total_context = len(context_symptoms_set)
        
        if total_context == 0:
            return 0
        
        # Proportional score
        ratio = overlap / total_context
        score = int(ratio * self.WEIGHTS["symptoms"])
        
        return score
    
    def calculate_tag_score(
        self,
        context: InterpretedContext,
        incident: Dict[str, Any]
    ) -> int:
        """
        Calculate tag and keyword overlap score.
        
        Args:
            context: Interpreted context
            incident: Candidate incident
            
        Returns:
            Tag overlap score
        """
        if not context.keywords:
            return 0
        
        # Get incident tags and searchText
        incident_tags = incident.get("tags", [])
        incident_search_text = incident.get("searchText", "")
        
        # Build searchable incident content
        incident_content = self.normalize_text(incident_search_text)
        
        # Count keyword matches
        matches = 0
        for keyword in context.keywords:
            normalized_keyword = self.normalize_text(keyword)
            if normalized_keyword in incident_content:
                matches += 1
        
        if len(context.keywords) == 0:
            return 0
        
        # Proportional score
        ratio = matches / len(context.keywords)
        score = int(ratio * self.WEIGHTS["tags"])
        
        return score
    
    def calculate_error_code_score(
        self,
        context: InterpretedContext,
        incident: Dict[str, Any]
    ) -> int:
        """
        Calculate error code match score.
        
        Args:
            context: Interpreted context
            incident: Candidate incident
            
        Returns:
            Error code match score
        """
        # For demo, we don't extract error codes from natural language
        # This would require regex patterns or ML extraction
        return 0
    
    def calculate_configuration_item_score(
        self,
        context: InterpretedContext,
        incident: Dict[str, Any]
    ) -> int:
        """
        Calculate configuration item match score.
        
        Args:
            context: Interpreted context
            incident: Candidate incident
            
        Returns:
            CI match score
        """
        # For demo, we don't extract CIs from natural language
        return 0
    
    def score_incident(
        self,
        context: InterpretedContext,
        incident: Dict[str, Any]
    ) -> ScoredIncident:
        """
        Score a single incident against the interpreted context.
        
        Args:
            context: Interpreted context
            incident: Candidate incident
            
        Returns:
            Scored incident with breakdown
        """
        breakdown = ScoreBreakdown(
            service=self.calculate_service_score(context, incident),
            application=self.calculate_application_score(context, incident),
            symptoms=self.calculate_symptom_score(context, incident),
            tags=self.calculate_tag_score(context, incident),
            error_code=self.calculate_error_code_score(context, incident),
            configuration_item=self.calculate_configuration_item_score(context, incident)
        )
        
        return ScoredIncident(
            incident=incident,
            score=breakdown.total,
            breakdown=breakdown
        )
    
    def score_and_rank(
        self,
        context: InterpretedContext,
        candidates: List[Dict[str, Any]],
        top_k: int = 3
    ) -> List[ScoredIncident]:
        """
        Score and rank all candidate incidents.
        
        Args:
            context: Interpreted context
            candidates: List of candidate incidents
            top_k: Number of top results to return
            
        Returns:
            Top K scored incidents, sorted by score descending
        """
        logger.info(f"Scoring {len(candidates)} candidates")
        
        # Score all candidates
        scored = [self.score_incident(context, incident) for incident in candidates]
        
        # Filter by minimum threshold
        scored = [s for s in scored if s.score >= self.min_score_threshold]
        
        # Sort by score descending
        scored.sort(key=lambda x: x.score, reverse=True)
        
        # Take top K
        top_results = scored[:top_k]
        
        logger.info(f"Top {len(top_results)} matches: {[(s.incident_id, s.score) for s in top_results]}")
        
        return top_results

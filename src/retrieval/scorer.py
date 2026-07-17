"""Deterministic scoring algorithm for incident matching."""
from typing import List, Dict, Any, Optional, Set
from dataclasses import dataclass
from src.retrieval.normalizer import InterpretedContext
from src.common.logging import get_logger

logger = get_logger(__name__)

# Minimum score used for a second-pass low-confidence retrieval.
FALLBACK_THRESHOLD = 15

# Adaptive threshold reduction when no service key is present in the query.
_NO_SERVICE_THRESHOLD_REDUCTION = 10

# Phrase-level boost rules.  Each rule fires when ALL required_keywords appear
# in the query AND at least one required tag or category matches the incident.
PHRASE_BOOST_RULES = [
    # SSL / certificate patterns
    {
        "required_keywords": {"ssl", "certificate"},
        "required_tags": {"ssl_certificate", "certificate_expiry"},
        "boost": 15,
    },
    {
        "required_keywords": {"expired", "certificate"},
        "required_tags": {"certificate_expiry"},
        "boost": 15,
    },
    {
        "required_keywords": {"ssl", "expired"},
        "required_tags": {"ssl_certificate", "certificate_expiry"},
        "boost": 15,
    },
    # Login failure patterns
    {
        "required_keywords": {"login", "failure"},
        "required_tags": {"login_failure"},
        "boost": 10,
    },
    # LDAP / directory patterns
    {
        "required_keywords": {"ldap"},
        "required_tags": {"ldap_timeout"},
        "boost": 12,
    },
    # Database connection pool patterns
    {
        "required_keywords": {"database", "connection"},
        "required_tags": {"connection_pool", "database"},
        "boost": 12,
    },
]


@dataclass
class ScoreBreakdown:
    """Detailed breakdown of scoring components."""
    service: int = 0
    application: int = 0
    symptoms: int = 0
    tags: int = 0
    error_code: int = 0
    configuration_item: int = 0
    phrase_boost: int = 0
    
    @property
    def total(self) -> int:
        """Calculate total score."""
        return (
            self.service +
            self.application +
            self.symptoms +
            self.tags +
            self.error_code +
            self.configuration_item +
            self.phrase_boost
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

    def calculate_phrase_boost(
        self,
        context: InterpretedContext,
        incident: Dict[str, Any]
    ) -> int:
        """
        Calculate phrase-level boost score.

        Fires PHRASE_BOOST_RULES when all required keywords from the query
        are present and the incident matches the expected tags/category.
        Capped at 15 to avoid overwhelming the weighted scoring.

        Args:
            context: Interpreted context
            incident: Candidate incident

        Returns:
            Phrase boost score (0-15)
        """
        if not context.keywords:
            return 0

        query_keywords = {self.normalize_text(k) for k in context.keywords}
        incident_tags = {t.lower() for t in incident.get("tags", [])}
        incident_category = incident.get("rootCauseCategory", "").lower()

        total_boost = 0
        for rule in PHRASE_BOOST_RULES:
            required_kw = {self.normalize_text(k) for k in rule["required_keywords"]}
            if not required_kw.issubset(query_keywords):
                continue

            required_tags = rule.get("required_tags", set())
            required_category = rule.get("required_category", set())

            tag_match = bool(required_tags & incident_tags)
            category_match = incident_category in {c.lower() for c in required_category}

            if required_tags and required_category:
                if tag_match or category_match:
                    total_boost += rule["boost"]
            elif required_tags:
                if tag_match:
                    total_boost += rule["boost"]
            elif required_category:
                if category_match:
                    total_boost += rule["boost"]

        return min(total_boost, 15)
    
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
            configuration_item=self.calculate_configuration_item_score(context, incident),
            phrase_boost=self.calculate_phrase_boost(context, incident)
        )
        
        return ScoredIncident(
            incident=incident,
            score=breakdown.total,
            breakdown=breakdown
        )
    
    def _effective_threshold(self, context: InterpretedContext, override: Optional[int] = None) -> int:
        """Return the score threshold to apply for this context."""
        if override is not None:
            return override
        if not context.service_key:
            # Lower the bar when service cannot be identified from the query
            return max(FALLBACK_THRESHOLD, self.min_score_threshold - _NO_SERVICE_THRESHOLD_REDUCTION)
        return self.min_score_threshold

    def score_and_rank(
        self,
        context: InterpretedContext,
        candidates: List[Dict[str, Any]],
        top_k: int = 3,
        override_threshold: Optional[int] = None
    ) -> List[ScoredIncident]:
        """
        Score and rank all candidate incidents.
        
        Args:
            context: Interpreted context
            candidates: List of candidate incidents
            top_k: Number of top results to return
            override_threshold: Explicit threshold override (e.g. for fallback pass)
            
        Returns:
            Top K scored incidents above threshold, sorted by score descending
        """
        threshold = self._effective_threshold(context, override_threshold)
        logger.info(f"Scoring {len(candidates)} candidates (threshold={threshold})")
        
        # Score all candidates
        scored = [self.score_incident(context, incident) for incident in candidates]
        
        # Filter by effective threshold
        scored = [s for s in scored if s.score >= threshold]
        
        # Sort by score descending
        scored.sort(key=lambda x: x.score, reverse=True)
        
        # Take top K
        top_results = scored[:top_k]
        
        logger.info(f"Top {len(top_results)} matches: {[(s.incident_id, s.score) for s in top_results]}")
        
        return top_results

    def get_fallback_matches(
        self,
        context: InterpretedContext,
        candidates: List[Dict[str, Any]],
        top_k: int = 3
    ) -> List[ScoredIncident]:
        """
        Return top-K low-confidence matches using FALLBACK_THRESHOLD.

        Called only when score_and_rank returns empty, to surface
        possible-but-uncertain candidates rather than a hard empty response.

        Args:
            context: Interpreted context
            candidates: Full candidate list
            top_k: Number of candidates to surface

        Returns:
            Top K scored incidents above FALLBACK_THRESHOLD, may be empty
        """
        logger.info("Running fallback match pass")
        return self.score_and_rank(context, candidates, top_k=top_k, override_threshold=FALLBACK_THRESHOLD)

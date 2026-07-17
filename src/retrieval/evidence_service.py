"""Main evidence retrieval service integrating all retrieval components."""
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field, asdict
from src.retrieval.normalizer import NaturalLanguageNormalizer, InterpretedContext
from src.retrieval.scorer import DeterministicScorer, ScoredIncident
from src.retrieval.correlation import ChangeCorrelator, ChangeCorrelationResult
from src.cosmos.client import CosmosDBClient
from src.cosmos.repositories import IncidentRepository, ChangeRepository
from src.api.settings import settings
from src.common.logging import get_logger
from src.common.exceptions import RetrievalException

logger = get_logger(__name__)


@dataclass
class HistoricalMatch:
    """A matched historical incident with scoring details."""
    incident_id: str
    similarity_score: int
    score_breakdown: Dict[str, int]
    incident_title: str
    root_cause: str
    root_cause_category: str
    linked_change_id: Optional[str] = None
    confidence: str = "high"  # "high" = passed strict threshold; "low" = fallback match


@dataclass
class RelatedChange:
    """A related change record with validation details."""
    change_id: str
    change_title: str
    validation_result: Optional[str] = None
    rollback_performed: bool = False
    post_implementation_issues: List[str] = field(default_factory=list)
    change_supported: bool = False


@dataclass
class EvidenceResult:
    """Complete evidence retrieval result."""
    interpreted_context: Dict[str, Any]
    historical_matches: List[HistoricalMatch]
    related_changes: List[RelatedChange]


class EvidenceRetrievalService:
    """
    Main service for retrieving grounded RCA evidence.
    
    Orchestrates:
    1. Natural language normalization
    2. Cosmos DB candidate retrieval
    3. Deterministic scoring
    4. Change correlation
    """
    
    def __init__(
        self,
        cosmos_client: CosmosDBClient,
        min_score_threshold: int = None
    ):
        """
        Initialize the evidence retrieval service.
        
        Args:
            cosmos_client: Cosmos DB client instance
            min_score_threshold: Minimum similarity score threshold
        """
        self.cosmos_client = cosmos_client
        self.incident_repo = IncidentRepository(cosmos_client)
        self.change_repo = ChangeRepository(cosmos_client)
        
        self.normalizer = NaturalLanguageNormalizer()
        self.scorer = DeterministicScorer(
            min_score_threshold=min_score_threshold or settings.min_similarity_threshold
        )
        self.correlator = ChangeCorrelator()
    
    def retrieve_candidates(
        self,
        context: InterpretedContext,
        max_candidates: int = None
    ) -> List[Dict[str, Any]]:
        """
        Retrieve candidate incidents from Cosmos DB.
        
        Args:
            context: Interpreted context
            max_candidates: Maximum number of candidates to retrieve
            
        Returns:
            List of candidate incident documents
        """
        max_candidates = max_candidates or settings.max_candidate_count
        
        # If service key is identified, query by partition
        if context.service_key:
            logger.info(f"Querying incidents for service: {context.service_key}")
            candidates = self.incident_repo.query_by_service(
                service_key=context.service_key,
                is_resolved=True,
                max_items=max_candidates
            )
        else:
            # Fall back to cross-partition query (less efficient)
            logger.warning("No service key identified, using cross-partition query")
            candidates = self.incident_repo.query_all_resolved(
                max_items=max_candidates
            )
        
        logger.info(f"Retrieved {len(candidates)} candidate incidents")
        return candidates
    
    def retrieve_change(
        self,
        change_id: str,
        service_key: str
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieve a change record by ID.
        
        Args:
            change_id: Change record ID
            service_key: Service partition key
            
        Returns:
            Change document or None if not found
        """
        if not change_id:
            return None
        
        try:
            change = self.change_repo.get_by_id(change_id, service_key)
            return change
        except Exception as e:
            logger.error(f"Error retrieving change {change_id}: {e}")
            return None
    
    def retrieve_evidence(
        self,
        incident_description: str,
        top_incident_count: int = 3
    ) -> EvidenceResult:
        """
        Retrieve grounded evidence for RCA.
        
        This is the main entry point for evidence retrieval.
        
        Args:
            incident_description: Natural language incident description
            top_incident_count: Number of top matches to return
            
        Returns:
            Complete evidence result with scored matches and related changes
        """
        logger.info(f"Starting evidence retrieval for: {incident_description[:50]}...")
        
        try:
            # Step 1: Interpret natural language
            context = self.normalizer.interpret(incident_description)
            
            # Step 2: Retrieve candidates
            candidates = self.retrieve_candidates(context)
            
            if not candidates:
                logger.warning("No candidate incidents found")
                return EvidenceResult(
                    interpreted_context=asdict(context),
                    historical_matches=[],
                    related_changes=[]
                )
            
            # Step 3: Score and rank
            scored_incidents = self.scorer.score_and_rank(
                context=context,
                candidates=candidates,
                top_k=top_incident_count
            )
            
            if not scored_incidents:
                logger.warning("No incidents met minimum score threshold — attempting fallback retrieval")
                fallback_incidents = self.scorer.get_fallback_matches(
                    context=context,
                    candidates=candidates,
                    top_k=top_incident_count
                )
                if not fallback_incidents:
                    logger.warning("No incidents met fallback threshold either")
                    return EvidenceResult(
                        interpreted_context=asdict(context),
                        historical_matches=[],
                        related_changes=[]
                    )
                # Build low-confidence matches — no change correlation for fallbacks
                historical_matches = [
                    HistoricalMatch(
                        incident_id=scored.incident_id,
                        similarity_score=scored.score,
                        score_breakdown={
                            "service": scored.breakdown.service,
                            "application": scored.breakdown.application,
                            "symptoms": scored.breakdown.symptoms,
                            "tags": scored.breakdown.tags,
                            "error_code": scored.breakdown.error_code,
                            "configuration_item": scored.breakdown.configuration_item,
                            "phrase_boost": scored.breakdown.phrase_boost,
                        },
                        incident_title=scored.incident_title,
                        root_cause=scored.root_cause,
                        root_cause_category=scored.root_cause_category,
                        linked_change_id=scored.linked_change_id,
                        confidence="low",
                    )
                    for scored in fallback_incidents
                ]
                logger.info(f"Fallback returned {len(historical_matches)} low-confidence match(es)")
                return EvidenceResult(
                    interpreted_context=asdict(context),
                    historical_matches=historical_matches,
                    related_changes=[]
                )
            
            # Step 4: Build historical matches
            historical_matches = []
            for scored in scored_incidents:
                match = HistoricalMatch(
                    incident_id=scored.incident_id,
                    similarity_score=scored.score,
                    score_breakdown={
                        "service": scored.breakdown.service,
                        "application": scored.breakdown.application,
                        "symptoms": scored.breakdown.symptoms,
                        "tags": scored.breakdown.tags,
                        "error_code": scored.breakdown.error_code,
                        "configuration_item": scored.breakdown.configuration_item,
                        "phrase_boost": scored.breakdown.phrase_boost,
                    },
                    incident_title=scored.incident_title,
                    root_cause=scored.root_cause,
                    root_cause_category=scored.root_cause_category,
                    linked_change_id=scored.linked_change_id,
                    confidence="high",
                )
                historical_matches.append(match)
            
            # Step 5: Correlate related changes
            related_changes = []
            for scored in scored_incidents:
                linked_change_id = scored.linked_change_id
                if not linked_change_id:
                    continue
                
                # Get service key from incident
                service_key = scored.incident.get("serviceKey", "")
                
                # Retrieve change
                change_doc = self.retrieve_change(linked_change_id, service_key)
                if not change_doc:
                    logger.warning(f"Linked change {linked_change_id} not found")
                    continue
                
                # Correlate change
                correlation = self.correlator.correlate_change(
                    incident=scored.incident,
                    change=change_doc
                )
                
                if correlation:
                    related_change = RelatedChange(
                        change_id=correlation.change_id,
                        change_title=correlation.change_title,
                        validation_result=correlation.validation_result,
                        rollback_performed=correlation.rollback_performed,
                        post_implementation_issues=correlation.post_implementation_issues,
                        change_supported=correlation.change_supported
                    )
                    related_changes.append(related_change)
            
            # Build result
            result = EvidenceResult(
                interpreted_context=asdict(context),
                historical_matches=historical_matches,
                related_changes=related_changes
            )
            
            logger.info(f"Evidence retrieval complete: {len(historical_matches)} matches, {len(related_changes)} changes")
            return result
            
        except Exception as e:
            logger.error(f"Error during evidence retrieval: {e}", exc_info=True)
            raise RetrievalException(f"Evidence retrieval failed: {e}")

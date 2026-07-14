"""Main FastAPI application."""
import uuid
from datetime import datetime, UTC
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware

from src.api.models import (
    HealthResponse, EvidenceRequest, EvidenceResponse,
    InterpretedContext, HistoricalMatch, RelatedChange, ScoreBreakdown
)
from src.api.settings import settings
from src.cosmos.client import get_cosmos_client, CosmosDBClient
from src.retrieval.evidence_service import EvidenceRetrievalService
from src.common.logging import setup_logging, get_logger
from src.common.exceptions import RetrievalException

# Initialize logging
setup_logging(settings.log_level)
logger = get_logger(__name__)

# Create FastAPI app
app = FastAPI(
    title="Incident RCA Evidence API",
    description="Retrieval API for incident root cause analysis",
    version="0.1.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Dependency for Cosmos DB client
def get_cosmos_db_client() -> CosmosDBClient:
    """Get or create Cosmos DB client."""
    try:
        return get_cosmos_client()
    except Exception as e:
        logger.error(f"Failed to get Cosmos DB client: {e}")
        raise HTTPException(status_code=503, detail="Database connection unavailable")


# Dependency for Evidence Retrieval Service
def get_evidence_service(
    cosmos_client: CosmosDBClient = Depends(get_cosmos_db_client)
) -> EvidenceRetrievalService:
    """Get Evidence Retrieval Service instance."""
    return EvidenceRetrievalService(cosmos_client)


@app.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """
    Health check endpoint.
    
    Returns:
        Health status including connectivity checks
    """
    logger.info("Health check requested")
    
    # TODO: Add actual Cosmos DB connectivity check in Phase 1
    cosmos_connected = bool(settings.azure_cosmos_endpoint)
    foundry_configured = bool(settings.azure_ai_project_endpoint)
    
    return HealthResponse(
        status="healthy" if cosmos_connected else "degraded",
        version="0.1.0",
        cosmos_connected=cosmos_connected,
        foundry_configured=foundry_configured
    )


@app.post("/api/rca/evidence", response_model=EvidenceResponse)
async def get_rca_evidence(
    request: EvidenceRequest,
    evidence_service: EvidenceRetrievalService = Depends(get_evidence_service)
) -> EvidenceResponse:
    """
    Retrieve grounded evidence for root cause analysis.
    
    Args:
        request: Evidence request with incident description
        evidence_service: Injected evidence retrieval service
        
    Returns:
        Evidence response with historical matches and related changes
    """
    request_id = str(uuid.uuid4())
    timestamp = datetime.now(UTC).isoformat().replace('+00:00', 'Z')
    
    logger.info(
        f"Evidence request received",
        extra={
            "request_id": request_id,
            "description_length": len(request.incident_description)
        }
    )
    
    try:
        # Retrieve evidence using the service
        evidence_result = evidence_service.retrieve_evidence(
            incident_description=request.incident_description,
            top_incident_count=request.top_incident_count
        )
        
        # Convert to API response models
        interpreted_context = InterpretedContext(**evidence_result.interpreted_context)
        
        historical_matches = [
            HistoricalMatch(
                incident_id=m.incident_id,
                similarity_score=m.similarity_score,
                score_breakdown=ScoreBreakdown(**m.score_breakdown),
                incident_title=m.incident_title,
                root_cause=m.root_cause,
                root_cause_category=m.root_cause_category,
                linked_change_id=m.linked_change_id
            )
            for m in evidence_result.historical_matches
        ]
        
        related_changes = [
            RelatedChange(
                change_id=c.change_id,
                change_title=c.change_title,
                validation_result=c.validation_result,
                rollback_performed=c.rollback_performed,
                post_implementation_issues=c.post_implementation_issues,
                change_supported=c.change_supported
            )
            for c in evidence_result.related_changes
        ]
        
        response = EvidenceResponse(
            interpreted_context=interpreted_context,
            historical_matches=historical_matches,
            related_changes=related_changes,
            request_id=request_id,
            timestamp=timestamp
        )
        
        logger.info(f"Evidence response generated: {request_id}")
        return response
        
    except RetrievalException as e:
        logger.error(f"Retrieval error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error(f"Error processing evidence request: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/")
async def root():
    """Root endpoint with API information."""
    return {
        "name": "Incident RCA Evidence API",
        "version": "0.1.0",
        "docs": "/docs",
        "health": "/health"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "src.api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=True
    )

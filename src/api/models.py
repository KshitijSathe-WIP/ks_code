"""Pydantic models for API requests and responses."""
from pydantic import BaseModel, Field
from typing import List, Optional


class HealthResponse(BaseModel):
    """Health check response."""
    status: str
    version: str
    cosmos_connected: bool
    foundry_configured: bool


class EvidenceRequest(BaseModel):
    """Request for incident evidence retrieval."""
    incident_description: str = Field(
        ...,
        description="Natural language description of the incident",
        min_length=5,
        max_length=500
    )
    top_incident_count: int = Field(
        default=3,
        ge=1,
        le=5,
        description="Maximum number of historical incidents to return"
    )


class InterpretedContext(BaseModel):
    """Interpreted context from natural language input."""
    business_service: Optional[str] = None
    service_key: Optional[str] = None
    probable_application: Optional[str] = None
    symptoms: List[str] = Field(default_factory=list)
    keywords: List[str] = Field(default_factory=list)


class ScoreBreakdown(BaseModel):
    """Detailed scoring breakdown for a historical match."""
    service: int = 0
    application: int = 0
    symptoms: int = 0
    tags: int = 0
    error_code: int = 0
    configuration_item: int = 0


class HistoricalMatch(BaseModel):
    """A matched historical incident with scoring details."""
    incident_id: str
    similarity_score: int
    score_breakdown: ScoreBreakdown
    incident_title: str
    root_cause: str
    root_cause_category: str
    linked_change_id: Optional[str] = None


class RelatedChange(BaseModel):
    """A related change record with validation details."""
    change_id: str
    change_title: str
    validation_result: Optional[str] = None
    rollback_performed: bool = False
    post_implementation_issues: List[str] = Field(default_factory=list)
    change_supported: bool = False


class EvidenceResponse(BaseModel):
    """Response containing grounded evidence for RCA."""
    interpreted_context: InterpretedContext
    historical_matches: List[HistoricalMatch]
    related_changes: List[RelatedChange]
    request_id: str
    timestamp: str


class RCAResponse(BaseModel):
    """Root cause analysis response from Foundry agent."""
    root_cause: str
    root_cause_category: str
    confidence: int = Field(ge=0, le=100)
    matched_incident_ids: List[str]
    related_change_id: str
    change_correlation: bool
    evidence: List[str]

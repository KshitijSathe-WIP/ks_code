"""Change correlation logic for validating incident-change relationships."""
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from src.common.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ChangeCorrelationResult:
    """Result of change correlation analysis."""
    change: Dict[str, Any]
    change_supported: bool
    reasons: List[str]
    
    @property
    def change_id(self) -> str:
        """Get change ID."""
        return self.change.get("changeId", "")
    
    @property
    def change_title(self) -> str:
        """Get change title."""
        return self.change.get("changeTitle", "")
    
    @property
    def validation_result(self) -> str:
        """Get validation result."""
        return self.change.get("validationResult", "")
    
    @property
    def rollback_performed(self) -> bool:
        """Get rollback status."""
        return self.change.get("rollbackPerformed", False)
    
    @property
    def post_implementation_issues(self) -> list[str]:
        """Get post-implementation issues."""
        return self.change.get("postImplementationIssues", [])


class ChangeCorrelator:
    """Validates and correlates change records with incidents."""
    
    def __init__(self):
        """Initialize the change correlator."""
        pass
    
    def normalize_text(self, text: str) -> str:
        """Normalize text for comparison."""
        if not text:
            return ""
        return text.lower().strip().replace("_", "-").replace(" ", "-")
    
    def validate_service_alignment(
        self,
        incident: Dict[str, Any],
        change: Dict[str, Any]
    ) -> bool:
        """
        Check if change and incident are for the same service/application.
        
        Args:
            incident: Incident document
            change: Change document
            
        Returns:
            True if service/application aligns
        """
        # Check service key
        incident_service = incident.get("serviceKey", "")
        change_service = change.get("serviceKey", "")
        
        if incident_service and change_service and incident_service == change_service:
            return True
        
        # Check business service
        incident_business = self.normalize_text(incident.get("businessService", ""))
        change_business = self.normalize_text(change.get("businessService", ""))
        
        if incident_business and change_business and incident_business == change_business:
            return True
        
        # Check application name
        incident_app = self.normalize_text(incident.get("applicationName", ""))
        change_app = self.normalize_text(change.get("applicationName", ""))
        
        if incident_app and change_app and incident_app in change_app:
            return True
        
        # Check configuration item
        incident_ci = self.normalize_text(incident.get("configurationItem", ""))
        change_ci = self.normalize_text(change.get("configurationItem", ""))
        
        if incident_ci and change_ci and incident_ci == change_ci:
            return True
        
        return False
    
    def validate_root_cause_support(
        self,
        incident: Dict[str, Any],
        change: Dict[str, Any]
    ) -> tuple[bool, List[str]]:
        """
        Check if change evidence supports the incident root cause.
        
        Args:
            incident: Incident document
            change: Change document
            
        Returns:
            Tuple of (is_supported, list of reasons)
        """
        reasons = []
        
        # Check root cause category alignment
        incident_category = self.normalize_text(incident.get("rootCauseCategory", ""))
        change_category = self.normalize_text(change.get("changeCategory", ""))
        
        if incident_category and change_category and incident_category == change_category:
            reasons.append(f"Change category matches root cause category: {change_category}")
        
        # Check validation result
        validation_result = change.get("validationResult", "")
        if validation_result in ["Partially Successful", "Failed"]:
            reasons.append(f"Change validation was {validation_result}")
        
        # Check if rollback was performed
        if change.get("rollbackPerformed", False):
            reasons.append("Change was rolled back")
        
        # Check post-implementation issues
        post_issues = change.get("postImplementationIssues", [])
        if post_issues:
            reasons.append(f"Post-implementation issues reported: {len(post_issues)} issues")
        
        # Check change correlation notes
        correlation_notes = change.get("changeCorrelationNotes", "")
        if correlation_notes:
            reasons.append("Change correlation notes indicate incident relationship")
        
        # Consider change supported if there's evidence of issues or correlation
        is_supported = len(reasons) > 0
        
        return is_supported, reasons
    
    def correlate_change(
        self,
        incident: Dict[str, Any],
        change: Optional[Dict[str, Any]]
    ) -> Optional[ChangeCorrelationResult]:
        """
        Perform full correlation analysis between incident and change.
        
        Args:
            incident: Incident document
            change: Change document (or None if not found)
            
        Returns:
            ChangeCorrelationResult or None if no change provided
        """
        if not change:
            return None
        
        logger.info(f"Correlating change {change.get('changeId')} with incident {incident.get('incidentId')}")
        
        reasons = []
        
        # Step 1: Validate service/application/CI alignment
        service_aligned = self.validate_service_alignment(incident, change)
        if not service_aligned:
            logger.warning(f"Service/application mismatch between incident and change")
            reasons.append("Service/application does not align")
            return ChangeCorrelationResult(
                change=change,
                change_supported=False,
                reasons=reasons
            )
        
        reasons.append("Service/application alignment confirmed")
        
        # Step 2: Validate root cause support
        root_cause_supported, support_reasons = self.validate_root_cause_support(incident, change)
        reasons.extend(support_reasons)
        
        # Determine overall support
        change_supported = service_aligned and root_cause_supported
        
        logger.info(f"Change correlation result: supported={change_supported}, reasons={len(reasons)}")
        
        return ChangeCorrelationResult(
            change=change,
            change_supported=change_supported,
            reasons=reasons
        )

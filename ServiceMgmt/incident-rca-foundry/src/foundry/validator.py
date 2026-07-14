"""Validator for Foundry RCA agent responses."""
import json
from typing import Dict, Any, List, Tuple
from pathlib import Path
import jsonschema
from src.common.logging import get_logger
from src.common.exceptions import ValidationException

logger = get_logger(__name__)


class RCAResponseValidator:
    """Validates RCA responses against the defined JSON schema."""
    
    def __init__(self):
        """Load the response schema from file."""
        schema_path = Path(__file__).parent / "response_schema.json"
        with open(schema_path, 'r') as f:
            self.schema = json.load(f)
        
        logger.info("Loaded RCA response schema")
    
    def validate(self, response: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """
        Validate an RCA response against the schema.
        
        Args:
            response: The RCA response dictionary to validate
            
        Returns:
            Tuple of (is_valid, list of error messages)
        """
        errors = []
        
        try:
            # Validate against JSON schema
            jsonschema.validate(instance=response, schema=self.schema)
            
            # Additional business logic validation
            errors.extend(self._validate_business_rules(response))
            
            is_valid = len(errors) == 0
            
            if is_valid:
                logger.info("RCA response validation passed")
            else:
                logger.warning(f"RCA response validation failed: {errors}")
            
            return is_valid, errors
            
        except jsonschema.ValidationError as e:
            error_msg = f"Schema validation failed: {e.message}"
            logger.error(error_msg)
            return False, [error_msg]
        except Exception as e:
            error_msg = f"Unexpected validation error: {str(e)}"
            logger.error(error_msg)
            return False, [error_msg]
    
    def _validate_business_rules(self, response: Dict[str, Any]) -> List[str]:
        """
        Validate business logic rules beyond schema validation.
        
        Args:
            response: The RCA response dictionary
            
        Returns:
            List of validation error messages
        """
        errors = []
        
        # Rule 1: If confidence is 0, matchedIncidentIds should be empty
        if response["confidence"] == 0 and len(response["matchedIncidentIds"]) > 0:
            errors.append(
                "Confidence is 0 but matchedIncidentIds is not empty"
            )
        
        # Rule 2: If changeCorrelation is true, relatedChangeId must not be empty
        if response["changeCorrelation"] and not response["relatedChangeId"]:
            errors.append(
                "changeCorrelation is true but relatedChangeId is empty"
            )
        
        # Rule 3: If relatedChangeId is not empty, it should be mentioned in evidence
        if response["relatedChangeId"]:
            change_mentioned = any(
                response["relatedChangeId"] in ev 
                for ev in response["evidence"]
            )
            if not change_mentioned:
                errors.append(
                    f"relatedChangeId {response['relatedChangeId']} not mentioned in evidence"
                )
        
        # Rule 4: Each matchedIncidentId should be mentioned in evidence
        for incident_id in response["matchedIncidentIds"]:
            if not any(incident_id in ev for ev in response["evidence"]):
                errors.append(
                    f"matchedIncidentId {incident_id} not mentioned in evidence"
                )
        
        # Rule 5: Evidence array should not be empty
        if len(response["evidence"]) == 0:
            errors.append("evidence array is empty")
        
        # Rule 6: rootCause should not be generic/vague if confidence is high
        if response["confidence"] >= 80:
            vague_terms = ["unknown", "unclear", "uncertain", "possibly", "maybe"]
            root_cause_lower = response["rootCause"].lower()
            if any(term in root_cause_lower for term in vague_terms):
                errors.append(
                    f"High confidence ({response['confidence']}) but rootCause contains vague terms"
                )
        
        return errors
    
    def validate_or_raise(self, response: Dict[str, Any]) -> None:
        """
        Validate and raise ValidationException if invalid.
        
        Args:
            response: The RCA response dictionary to validate
            
        Raises:
            ValidationException: If validation fails
        """
        is_valid, errors = self.validate(response)
        if not is_valid:
            error_msg = "RCA response validation failed: " + "; ".join(errors)
            raise ValidationException(error_msg)

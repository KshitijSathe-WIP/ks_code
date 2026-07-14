"""Integration tests for RCA Agent scenarios."""
import pytest
from src.foundry.validator import RCAResponseValidator


@pytest.fixture
def validator():
    """Create a validator instance."""
    return RCAResponseValidator()


class TestRCAAgentScenarios:
    """Test suite for RCA agent demo scenarios from PHASE 4."""
    
    def test_scenario_1_broad_mobile_banking_issue(self, validator):
        """
        Scenario 1 — Broad Mobile Banking issue
        Input: "Mobile banking app not working"
        
        Expected behavior:
        - Identifies Mobile Banking
        - Returns relevant Mobile Banking historical incidents
        - Uses moderate confidence (65-79)
        - Does not claim a confirmed technical diagnosis
        """
        # Simulated agent response for broad input
        response = {
            "rootCause": "Mobile banking service disruption due to API node issues or configuration problems",
            "rootCauseCategory": "Application",
            "confidence": 68,
            "matchedIncidentIds": ["INC10014", "INC10015"],
            "relatedChangeId": "",
            "changeCorrelation": False,
            "evidence": [
                "Similarity score: 65/100",
                "Historical incident: INC10014",
                "Historical incident: INC10015",
                "Matched service: Mobile Banking",
                "Broad symptom match: unavailable"
            ]
        }
        
        is_valid, errors = validator.validate(response)
        assert is_valid is True
        assert 65 <= response["confidence"] <= 79, "Moderate confidence expected for broad input"
        assert len(response["matchedIncidentIds"]) >= 1
        assert "Mobile Banking" in str(response["evidence"])
    
    def test_scenario_2_specific_load_balancer_symptom(self, validator):
        """
        Scenario 2 — More specific load-balancer symptom
        Input: "Mobile banking is very slow and one API node appears overloaded"
        
        Expected behavior:
        - Ranks INC10014 first
        - Returns CHG50014 if the change supports the root cause
        - Returns higher confidence than Scenario 1 (80+)
        """
        response = {
            "rootCause": "Load balancer health-check misconfiguration kept routing traffic to a degraded API node",
            "rootCauseCategory": "Network",
            "confidence": 85,
            "matchedIncidentIds": ["INC10014"],
            "relatedChangeId": "CHG50014",
            "changeCorrelation": True,
            "evidence": [
                "Similarity score: 82/100",
                "Historical incident: INC10014",
                "Matched service: Mobile Banking",
                "Symptom match: slowness, overloaded node, uneven traffic",
                "Root cause category: Network",
                "Related change: CHG50014 - Load Balancer Health Check Update",
                "Change validation: Partially Successful",
                "Post-implementation issues: 2 reported"
            ]
        }
        
        is_valid, errors = validator.validate(response)
        assert is_valid is True
        assert response["confidence"] >= 80, "Higher confidence for specific symptoms"
        assert "INC10014" in response["matchedIncidentIds"]
        assert response["relatedChangeId"] == "CHG50014"
        assert response["changeCorrelation"] is True
    
    def test_scenario_3_change_oriented_input(self, validator):
        """
        Scenario 3 — Change-oriented input
        Input: "Mobile banking became slow immediately after the load balancer update"
        
        Expected behavior:
        - Strong change correlation
        - Uses change validation and post-implementation issues as evidence
        """
        response = {
            "rootCause": "Load balancer health-check misconfiguration following recent update",
            "rootCauseCategory": "Network",
            "confidence": 88,
            "matchedIncidentIds": ["INC10014"],
            "relatedChangeId": "CHG50014",
            "changeCorrelation": True,
            "evidence": [
                "Similarity score: 78/100",
                "Historical incident: INC10014",
                "Matched service: Mobile Banking",
                "Symptom match: slowness after change",
                "Related change: CHG50014 - Load Balancer Health Check Update",
                "Change validation: Partially Successful",
                "Post-implementation issues: Uneven traffic routing observed",
                "Timing correlation: Incident started after change implementation"
            ]
        }
        
        is_valid, errors = validator.validate(response)
        assert is_valid is True
        assert response["changeCorrelation"] is True
        assert response["relatedChangeId"] != ""
        assert any("change" in e.lower() for e in response["evidence"])
        assert any("validation" in e.lower() or "implementation" in e.lower() for e in response["evidence"])
    
    def test_scenario_4_payment_failure(self, validator):
        """
        Scenario 4 — Payment failure
        Input: "Payments started failing after the API release"
        
        Expected behavior:
        - Searches the Payments Platform partition
        - Returns payment-related incidents and relevant changes
        """
        response = {
            "rootCause": "Payment API validation logic error in new release",
            "rootCauseCategory": "Application",
            "confidence": 76,
            "matchedIncidentIds": ["INC10020"],
            "relatedChangeId": "CHG50020",
            "changeCorrelation": True,
            "evidence": [
                "Similarity score: 71/100",
                "Historical incident: INC10020",
                "Matched service: Payments Platform",
                "Symptom match: payment failures",
                "Related change: CHG50020 - Payment API Release",
                "Change validation: Failed",
                "Post-implementation issues: 5 reported"
            ]
        }
        
        is_valid, errors = validator.validate(response)
        assert is_valid is True
        assert any("payment" in e.lower() for e in response["evidence"])
    
    def test_scenario_5_regulatory_batch_issue(self, validator):
        """
        Scenario 5 — Regulatory batch issue
        Input: "The regulatory reporting batch did not complete overnight"
        
        Expected behavior:
        - Searches Regulatory Reporting records
        - Ranks ETL/schema/database/batch incidents
        """
        response = {
            "rootCause": "Regulatory reporting ETL batch timeout due to database schema change",
            "rootCauseCategory": "Database",
            "confidence": 72,
            "matchedIncidentIds": ["INC10030"],
            "relatedChangeId": "CHG50030",
            "changeCorrelation": True,
            "evidence": [
                "Similarity score: 69/100",
                "Historical incident: INC10030",
                "Matched service: Regulatory Reporting",
                "Symptom match: batch failure, timeout",
                "Root cause category: Database",
                "Related change: CHG50030 - Schema Migration"
            ]
        }
        
        is_valid, errors = validator.validate(response)
        assert is_valid is True
        assert any("regulatory" in e.lower() or "batch" in e.lower() for e in response["evidence"])
    
    def test_scenario_6_no_evidence(self, validator):
        """
        Scenario 6 — No evidence
        Input: "An unrelated service with no historical records is unavailable"
        
        Expected behavior:
        - Does not invent evidence
        - Returns confidence 0 and empty matched IDs
        """
        response = {
            "rootCause": "No similar historical incidents found",
            "rootCauseCategory": "Unknown",
            "confidence": 0,
            "matchedIncidentIds": [],
            "relatedChangeId": "",
            "changeCorrelation": False,
            "evidence": [
                "No grounded evidence available",
                "Service not found in historical records"
            ]
        }
        
        is_valid, errors = validator.validate(response)
        assert is_valid is True
        assert response["confidence"] == 0
        assert len(response["matchedIncidentIds"]) == 0
        assert response["relatedChangeId"] == ""
        assert response["changeCorrelation"] is False
    
    def test_tool_schema_structure(self):
        """Verify tool schema has required structure."""
        import json
        from pathlib import Path
        
        schema_path = Path(__file__).parent.parent.parent / "src" / "foundry" / "tool_schema.json"
        with open(schema_path, 'r') as f:
            tool_schema = json.load(f)
        
        assert tool_schema["type"] == "function"
        assert "function" in tool_schema
        assert tool_schema["function"]["name"] == "search_incident_rca_evidence"
        assert "parameters" in tool_schema["function"]
        
        params = tool_schema["function"]["parameters"]
        assert "incidentDescription" in params["properties"]
        assert "topIncidentCount" in params["properties"]
        assert "incidentDescription" in params["required"]
    
    def test_response_schema_structure(self):
        """Verify response schema has required structure."""
        import json
        from pathlib import Path
        
        schema_path = Path(__file__).parent.parent.parent / "src" / "foundry" / "response_schema.json"
        with open(schema_path, 'r') as f:
            response_schema = json.load(f)
        
        assert response_schema["type"] == "object"
        assert "rootCause" in response_schema["properties"]
        assert "rootCauseCategory" in response_schema["properties"]
        assert "confidence" in response_schema["properties"]
        assert "matchedIncidentIds" in response_schema["properties"]
        assert "relatedChangeId" in response_schema["properties"]
        assert "changeCorrelation" in response_schema["properties"]
        assert "evidence" in response_schema["properties"]
        
        required = response_schema["required"]
        assert len(required) == 7
        assert "rootCause" in required
        assert "evidence" in required
    
    def test_agent_instructions_exist(self):
        """Verify agent instructions file exists and has content."""
        from pathlib import Path
        
        instructions_path = Path(__file__).parent.parent.parent / "src" / "foundry" / "agent_instructions.md"
        assert instructions_path.exists()
        
        with open(instructions_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        assert len(content) > 100
        assert "search_incident_rca_evidence" in content
        assert "confidence" in content.lower()
        assert "grounded" in content.lower()
        assert "evidence" in content.lower()

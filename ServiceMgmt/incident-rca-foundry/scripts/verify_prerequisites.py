"""Verify that all prerequisites are met for the RCA system."""
import sys
import os
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from azure.identity import DefaultAzureCredential
from azure.cosmos import CosmosClient
from src.api.settings import settings
from src.common.logging import setup_logging, get_logger

setup_logging(settings.log_level)
logger = get_logger(__name__)


def check_azure_authentication() -> bool:
    """Check if Azure authentication is working."""
    try:
        logger.info("Checking Azure authentication...")
        credential = DefaultAzureCredential()
        # Try to get a token for Cosmos DB
        token = credential.get_token("https://cosmos.azure.com/.default")
        logger.info("✓ Azure authentication successful")
        return True
    except Exception as e:
        logger.error(f"✗ Azure authentication failed: {e}")
        return False


def check_cosmos_connectivity() -> bool:
    """Check if Cosmos DB is reachable."""
    try:
        logger.info("Checking Cosmos DB connectivity...")
        credential = DefaultAzureCredential()
        client = CosmosClient(settings.azure_cosmos_endpoint, credential=credential)
        # List databases to verify connection
        list(client.list_databases())
        logger.info("✓ Cosmos DB connection successful")
        return True
    except Exception as e:
        logger.error(f"✗ Cosmos DB connection failed: {e}")
        return False


def check_environment_variables() -> bool:
    """Check if required environment variables are set."""
    logger.info("Checking environment variables...")
    required_vars = [
        "AZURE_COSMOS_ENDPOINT",
        "AZURE_COSMOS_DATABASE"
    ]
    
    missing = []
    for var in required_vars:
        value = getattr(settings, var.lower(), None)
        if not value or "your-" in str(value):
            missing.append(var)
    
    if missing:
        logger.error(f"✗ Missing or placeholder environment variables: {', '.join(missing)}")
        return False
    
    logger.info("✓ Environment variables configured")
    return True


def check_foundry_configuration() -> bool:
    """Check if Foundry configuration is present."""
    logger.info("Checking Foundry configuration...")
    
    if not settings.azure_ai_project_endpoint:
        logger.warning("⚠ Foundry project endpoint not configured (optional for Phase 0)")
        return True
    
    if "your-" in settings.azure_ai_project_endpoint:
        logger.warning("⚠ Foundry endpoint appears to be placeholder")
        return True
    
    logger.info("✓ Foundry configuration present")
    return True


def main():
    """Run all prerequisite checks."""
    logger.info("=" * 60)
    logger.info("Incident RCA System - Prerequisites Verification")
    logger.info("=" * 60)
    
    checks = [
        ("Environment Variables", check_environment_variables),
        ("Azure Authentication", check_azure_authentication),
        ("Cosmos DB Connectivity", check_cosmos_connectivity),
        ("Foundry Configuration", check_foundry_configuration)
    ]
    
    results = {}
    for name, check_func in checks:
        logger.info(f"\n--- {name} ---")
        results[name] = check_func()
    
    logger.info("\n" + "=" * 60)
    logger.info("Summary:")
    logger.info("=" * 60)
    
    for name, passed in results.items():
        status = "✓ PASSED" if passed else "✗ FAILED"
        logger.info(f"{name}: {status}")
    
    all_passed = all(results.values())
    
    if all_passed:
        logger.info("\n✓ All prerequisite checks passed!")
        logger.info("You can proceed with database initialization.")
        return 0
    else:
        logger.error("\n✗ Some prerequisite checks failed.")
        logger.error("Please fix the issues above before proceeding.")
        return 1


if __name__ == "__main__":
    sys.exit(main())

"""Application settings and configuration."""
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


class Settings(BaseSettings):
    """Application configuration loaded from environment variables."""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )
    
    # Cosmos DB Configuration
    azure_cosmos_endpoint: Optional[str] = None
    azure_cosmos_key: Optional[str] = None  # If not provided, uses DefaultAzureCredential
    azure_cosmos_database: str = "IncidentRCA"
    azure_cosmos_incident_container: str = "historical-incidents"
    azure_cosmos_change_container: str = "change-records"
    
    # Microsoft Foundry Configuration
    azure_ai_project_endpoint: Optional[str] = None
    azure_ai_model_deployment_name: Optional[str] = None
    
    # Application Configuration
    log_level: str = "INFO"
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    
    # API Key Authentication (set RCA_API_KEY env var on App Service)
    rca_api_key: Optional[str] = None

    # Retrieval Configuration
    max_candidate_count: int = 25
    top_incident_count: int = 3
    min_similarity_threshold: int = 30


# Global settings instance
settings = Settings()

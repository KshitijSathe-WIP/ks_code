"""Custom exception classes for the RCA system."""


class RCAException(Exception):
    """Base exception for all RCA system errors."""
    pass


class CosmosDBException(RCAException):
    """Raised when Cosmos DB operations fail."""
    pass


class ValidationException(RCAException):
    """Raised when data validation fails."""
    pass


class FoundryException(RCAException):
    """Raised when Foundry agent operations fail."""
    pass


class RetrievalException(RCAException):
    """Raised when evidence retrieval fails."""
    pass

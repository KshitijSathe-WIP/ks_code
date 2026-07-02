# active_version.py
# ────────────────────────────────────────────────────────────
# Resolves the currently ACTIVE lineage version_id from the
# version_registry Cosmos DB container.
#
# Returns None gracefully when versioning has not been set up yet
# so that all existing query tools continue to work unchanged.
#
# Result is cached for 60 seconds (TTL) to avoid a Cosmos round-trip
# on every single agent tool call.
#
# Uses a shared CosmosClient singleton (lazily created) so that
# cosmos_tools.py and this module share one connection pool.
# ────────────────────────────────────────────────────────────

import os
import logging
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logger = logging.getLogger(__name__)

_DATABASE  = "lineage"
_CONTAINER = "version_registry"
_TTL_SECONDS = 60

# Module-level cache — Cosmos active version
_cached_version_id: str | None = None
_cache_expires_at:  datetime   = datetime.min

# Module-level cache — Neo4j graph version (read from the graph itself)
_neo4j_version_id: str | None = None
_neo4j_cache_expires: datetime = datetime.min

# Module-level cache — Cosmos data version (base full-load version for queries)
_cosmos_data_version_id: str | None = None
_cosmos_data_cache_expires: datetime = datetime.min

# Shared singleton for the version_registry container
_vr_container = None


def _get_vr_container():
    """Lazily initialise the version_registry container client.
    Re-uses the same CosmosClient instance as cosmos_tools (imported at
    call time to break circular import) so we share a single TCP pool."""
    global _vr_container
    if _vr_container is not None:
        return _vr_container

    # Lazy import — cosmos_tools may not be on sys.path during module load.
    try:
        from cosmos_tools import _get_container as _get_td_container
        # Calling _get_td_container() ensures _cosmos_client is created.
        _get_td_container()
        from cosmos_tools import _cosmos_client
        db = _cosmos_client.get_database_client(_DATABASE)
        _vr_container = db.get_container_client(_CONTAINER)
        return _vr_container
    except Exception:
        # Fallback: create our own client (first-time or standalone usage)
        from azure.cosmos import CosmosClient
        endpoint = os.environ.get("COSMOS_ENDPOINT")
        key      = os.environ.get("COSMOS_KEY")
        if not endpoint or not key:
            return None
        client = CosmosClient(url=endpoint, credential=key)
        db = client.get_database_client(_DATABASE)
        _vr_container = db.get_container_client(_CONTAINER)
        return _vr_container


def get() -> str | None:
    """
    Return the version_id of the current ACTIVE lineage version.

    Returns None if:
      - version_registry container does not exist yet (pre-versioning state)
      - no ACTIVE version is registered
      - Cosmos credentials are missing

    Callers should treat None as "no version filter — query all data"
    so that existing lineage tools work without modification.
    """
    global _cached_version_id, _cache_expires_at

    now = datetime.utcnow()
    if now < _cache_expires_at and _cached_version_id is not None:
        return _cached_version_id

    try:
        ct = _get_vr_container()
        if ct is None:
            return None

        results = list(ct.query_items(
            query="SELECT TOP 1 c.id FROM c WHERE c.status = 'ACTIVE'",
            enable_cross_partition_query=True,
        ))

        if results:
            _cached_version_id = results[0]["id"]
            _cache_expires_at  = now + timedelta(seconds=_TTL_SECONDS)
            logger.debug("active_version resolved: %s", _cached_version_id)
            return _cached_version_id

        return None

    except Exception as exc:
        # Silently degrade — existing tools must not break if versioning
        # is not yet configured.
        logger.debug("active_version.get() skipped: %s", exc)
        return None


def get_cosmos_data_version() -> str | None:
    """
    Return the version_id to use when filtering Cosmos transformation_details
    queries.

    Patch versions (change_type = "PATCH", created by change_manager) only
    contain delta audit records — the full transformation documents live in the
    base full-load version.  This function resolves the correct version:

      - PATCH version active  → return base_active_version_id  (the full-load base)
      - Full-load version active → return the version itself

    This means Cosmos queries always target the version that actually holds the
    9,000+ full documents, regardless of how many patch versions have been
    approved on top of it.
    """
    global _cosmos_data_version_id, _cosmos_data_cache_expires

    now = datetime.utcnow()
    if now < _cosmos_data_cache_expires and _cosmos_data_version_id is not None:
        return _cosmos_data_version_id

    try:
        ct = _get_vr_container()
        if ct is None:
            return None

        results = list(ct.query_items(
            query=(
                "SELECT TOP 1 c.id, c.change_type, c.base_active_version_id "
                "FROM c WHERE c.status = 'ACTIVE'"
            ),
            enable_cross_partition_query=True,
        ))

        if results:
            doc = results[0]
            if doc.get("change_type") == "PATCH" and doc.get("base_active_version_id"):
                # Patch version — full docs are in the base
                _cosmos_data_version_id = doc["base_active_version_id"]
            else:
                # Full-load version — full docs are here
                _cosmos_data_version_id = doc["id"]
            _cosmos_data_cache_expires = now + timedelta(seconds=_TTL_SECONDS)
            logger.debug(
                "cosmos_data_version resolved: %s (active: %s, change_type: %s)",
                _cosmos_data_version_id,
                doc.get("id"),
                doc.get("change_type"),
            )
            return _cosmos_data_version_id

        return None

    except Exception as exc:
        logger.debug("get_cosmos_data_version() skipped: %s", exc)
        return None


def get_neo4j_version() -> str | None:
    """
    Return the version_id currently tagged on Neo4j Field nodes.

    Reads directly from the graph (one cheap DISTINCT query) so that
    Neo4j lineage queries always filter on the version that is actually
    loaded — regardless of which Cosmos version is currently ACTIVE.
    Cached for the same TTL as the Cosmos version.

    Returns None if Neo4j is unreachable or nodes have no version_id.
    """
    global _neo4j_version_id, _neo4j_cache_expires

    now = datetime.utcnow()
    if now < _neo4j_cache_expires and _neo4j_version_id is not None:
        return _neo4j_version_id

    try:
        import json
        from neo4j_client import Neo4jLineageClient
        _client = Neo4jLineageClient()
        rows = json.loads(
            _client.run_cypher(
                "MATCH (f:Field) WHERE f.version_id IS NOT NULL "
                "RETURN DISTINCT f.version_id AS version_id LIMIT 1"
            )
        )
        if rows:
            _neo4j_version_id = rows[0]["version_id"]
            _neo4j_cache_expires = now + timedelta(seconds=_TTL_SECONDS)
            logger.debug("neo4j_version resolved: %s", _neo4j_version_id)
            return _neo4j_version_id
    except Exception as exc:
        logger.debug("get_neo4j_version() skipped: %s", exc)

    return None


def invalidate() -> None:
    """
    Force the next get() call to re-read from Cosmos DB.
    Called by approve_version() immediately after promotion so that
    subsequent agent queries see the new version within one request.
    """
    global _cached_version_id, _cache_expires_at
    global _neo4j_version_id, _neo4j_cache_expires
    global _cosmos_data_version_id, _cosmos_data_cache_expires
    _cached_version_id       = None
    _cache_expires_at        = datetime.min
    _neo4j_version_id        = None
    _neo4j_cache_expires     = datetime.min
    _cosmos_data_version_id  = None
    _cosmos_data_cache_expires = datetime.min
    logger.debug("active_version cache invalidated")

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Status vocabulary — extend only via config, never free-form in code
# ---------------------------------------------------------------------------
VALID_STATUSES = frozenset([
    "Need Profiles",
    "L1 in Progress",
    "Pending CI FB",
    "Pending CI L2",
    "Pending Offer",
    "Pending Joiner",
    "Joined",
    "Project",
    "To be deleted",
])

# ---------------------------------------------------------------------------
# Load config once at import time
# ---------------------------------------------------------------------------
_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.json"

def load_config() -> dict:
    with open(_CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)

CONFIG = load_config()


# ---------------------------------------------------------------------------
# Domain dataclasses
# ---------------------------------------------------------------------------

@dataclass
class OIRDemand:
    demand_id: str
    project: str
    sldu: str
    role: str
    skill: str
    status: str
    pm_name: str
    pm_email: str
    tm_name: str
    tm_email: str
    em_name: str
    em_email: str
    dem_start_date: Optional[date]
    dem_end_date: Optional[date]
    comments: str
    remarks_status: str
    comments_hash: str
    last_content_change_date: date
    stale_days: int = 0
    last_notified_on: Optional[datetime] = None
    escalation_level: int = 0
    snooze_until: Optional[datetime] = None
    source_file: str = ""
    first_seen_date: Optional[date] = None
    is_active: bool = True


@dataclass
class OIRSnapshot:
    snapshot_id: str
    demand_id: str
    snapshot_date: date
    status: str
    comments: str
    remarks_status: str
    comments_hash: str
    dem_end_date: Optional[date]
    pm_email: str
    tm_email: str
    source_file: str
    ingested_at: datetime


@dataclass
class InteractionLog:
    interaction_id: str
    demand_id: str
    event_type: str          # NOTIFIED | REPLIED | NO_CHANGE | SNOOZED | ESCALATED | AUTO_UPDATED | REJECTED
    recipient_email: str
    actor_email: str
    channel: str = "TEAMS"
    rule_triggered: str = ""
    message_sent: str = ""
    reply_raw: str = ""
    reply_parsed: Optional[dict] = None
    confidence: Optional[float] = None
    field_changed: str = ""
    value_before: str = ""
    value_after: str = ""
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class ParsedReply:
    demand_id: str
    confidence: float
    comments: Optional[str] = None
    remarks_status: Optional[str] = None
    dem_end_date: Optional[str] = None      # ISO 8601 string
    no_change: bool = False
    clarification_needed: Optional[str] = None


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class IngestionError(RuntimeError):
    """Raised when the OIR file cannot be ingested safely."""


class AuthorisationError(PermissionError):
    """Raised when an actor is not authorised to update a demand."""


class ValidationError(ValueError):
    """Raised when a field value violates business rules."""

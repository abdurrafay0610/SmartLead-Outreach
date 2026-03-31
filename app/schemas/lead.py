import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, EmailStr, Field


# --- Lead + Email Injection Schemas ---


class LeadEmailInput(BaseModel):
    """Single lead with pre-generated email content."""

    email: EmailStr
    subject: str = Field(..., min_length=1)
    body_html: str = Field(..., min_length=1)
    body_text: Optional[str] = None

    # Optional lead metadata
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    company: Optional[str] = None
    linkedin_url: Optional[str] = None

    # Optional LLM provenance (if caller wants to track it)
    prompt_version: Optional[str] = None
    model_name: Optional[str] = None
    context_snapshot: Optional[dict[str, Any]] = None


class LeadInjectRequest(BaseModel):
    """Batch inject leads with their personalized emails into a campaign."""

    leads: list[LeadEmailInput] = Field(..., min_length=1, max_length=5000)


class LeadInjectResponse(BaseModel):
    campaign_id: uuid.UUID
    total_received: int
    total_created: int
    total_skipped_duplicate: int
    message: str


class LeadResponse(BaseModel):
    id: uuid.UUID
    email: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    company: Optional[str] = None
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class OutboundMessageResponse(BaseModel):
    id: uuid.UUID
    step_number: int
    subject: str
    body_html: str
    body_text: Optional[str] = None
    message_status: str
    provider_message_id: Optional[str] = None
    prompt_version: Optional[str] = None
    model_name: Optional[str] = None
    generated_at: Optional[datetime] = None
    sent_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class MessageEventResponse(BaseModel):
    id: uuid.UUID
    event_type: str
    provider_event_id: Optional[str] = None
    event_time: Optional[datetime] = None
    received_at: datetime
    payload_json: Optional[dict[str, Any]] = None

    model_config = {"from_attributes": True}


class LeadInteractionResponse(BaseModel):
    """Full timeline for a single lead across campaigns."""

    lead: LeadResponse
    campaigns: list[dict[str, Any]]  # campaign info + messages + events

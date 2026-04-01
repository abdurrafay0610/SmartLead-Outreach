import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, EmailStr, Field, model_validator


# --- Step Email Schema (one email per sequence step) ---


class StepEmail(BaseModel):
    """Email content for a single sequence step."""

    step_number: int = Field(
        ...,
        ge=1,
        le=10,
        description="Which sequence step this email belongs to (1-based).",
    )
    subject: str = Field(..., min_length=1)
    body_html: str = Field(..., min_length=1)
    body_text: Optional[str] = None

    # Optional LLM provenance per step
    prompt_version: Optional[str] = None
    model_name: Optional[str] = None
    context_snapshot: Optional[dict[str, Any]] = None


# --- Lead + Email Injection Schemas ---


class LeadEmailInput(BaseModel):
    """
    Single lead with pre-generated email content for ALL sequence steps.

    For a campaign with num_emails_per_lead=3, you must provide exactly
    3 entries in the `emails` list with step_numbers 1, 2, and 3.
    """

    email: EmailStr

    # All emails for this lead (one per sequence step)
    emails: list[StepEmail] = Field(
        ...,
        min_length=1,
        max_length=10,
        description="Pre-generated email content for each sequence step. "
                    "Must provide exactly num_emails_per_lead entries with "
                    "step_numbers 1 through N.",
    )

    # Optional lead metadata
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    company: Optional[str] = None
    linkedin_url: Optional[str] = None

    @model_validator(mode="after")
    def validate_step_numbers(self) -> "LeadEmailInput":
        """Ensure step numbers are sequential starting from 1 with no gaps."""
        step_numbers = sorted(e.step_number for e in self.emails)
        expected = list(range(1, len(self.emails) + 1))
        if step_numbers != expected:
            raise ValueError(
                f"Step numbers must be sequential starting from 1. "
                f"Got {step_numbers}, expected {expected}."
            )
        return self


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
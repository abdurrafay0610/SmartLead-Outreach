import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# --- Campaign Schemas ---


class AssignSenderRequest(BaseModel):
    """
    Assign sender email account(s) to a campaign on Smartlead.
    """
    email_account_ids: list[int] = Field(
        ...,
        min_length=1,
        description="Smartlead email account IDs (integers). "
                    "Get these from the /sender-accounts/list endpoint.",
        examples=[[12345], [12345, 67890]],
    )


class CampaignCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=500)
    persona: Optional[str] = None
    segment: Optional[str] = None
    num_emails_per_lead: int = Field(
        default=1,
        ge=1,
        le=10,
        description="Number of emails (sequence steps) each lead will receive in this campaign. "
                    "When injecting leads, you must provide exactly this many emails per lead.",
        examples=[1, 3],
    )


class SequenceStepDelay(BaseModel):
    """Delay configuration for a single sequence step."""
    step_number: int = Field(..., ge=1, le=10, description="Step number (1-based)")
    delay_in_days: int = Field(
        ...,
        ge=0,
        description="Days to wait after the previous step before sending this email. "
                    "Step 1 should always be 0.",
        examples=[0, 3, 5],
    )


class SequenceSetupRequest(BaseModel):
    """
    Configure sequence step delays for the campaign.
    If not provided, defaults will be used:
    step 1 = 0 days, step 2 = 3 days, step 3 = 5 days, etc.
    """
    step_delays: Optional[list[SequenceStepDelay]] = Field(
        None,
        description="Optional per-step delay configuration. "
                    "Must provide exactly num_emails_per_lead entries. "
                    "If omitted, default delays are used (step 1=0, step 2=3, step 3+=5 days).",
    )


class ScheduleConfig(BaseModel):
    timezone: str = Field(..., examples=["America/New_York"])
    days_of_the_week: list[int] = Field(
        ...,
        examples=[[1, 2, 3, 4, 5]],
        description="0=Sun, 1=Mon, 2=Tue, 3=Wed, 4=Thu, 5=Fri, 6=Sat"
    )
    start_hour: str = Field(..., examples=["09:00"])
    end_hour: str = Field(..., examples=["17:00"])
    min_time_btw_emails: int = Field(
        ...,
        description="Minutes between consecutive emails"
    )
    max_new_leads_per_day: Optional[int] = Field(
        None,
        description="Maximum new leads to email per day"
    )


class CampaignSettingsRequest(BaseModel):
    schedule: Optional[ScheduleConfig] = None
    sender_account_id: Optional[uuid.UUID] = None
    max_email_per_day: Optional[int] = None


class CampaignStatusRequest(BaseModel):
    status: str = Field(..., pattern="^(start|pause|stop)$", description="start, pause, or stop")


class CampaignResponse(BaseModel):
    id: uuid.UUID
    name: str
    persona: Optional[str] = None
    segment: Optional[str] = None
    status: str
    num_emails_per_lead: int = 1
    provider_campaign_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CampaignDetailResponse(CampaignResponse):
    total_leads: int = 0
    total_sent: int = 0
    total_opened: int = 0
    total_replied: int = 0
    total_bounced: int = 0
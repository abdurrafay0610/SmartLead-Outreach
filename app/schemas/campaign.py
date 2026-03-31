import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# --- Campaign Schemas ---


class CampaignCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=500)
    persona: Optional[str] = None
    segment: Optional[str] = None


class ScheduleConfig(BaseModel):
    timezone: str = Field(..., examples=["America/New_York"])
    days: list[int] = Field(..., examples=[[1, 2, 3, 4, 5]], description="1=Mon, 7=Sun")
    start_hour: str = Field(..., examples=["09:00"])
    end_hour: str = Field(..., examples=["17:00"])
    min_time_btw_emails: Optional[int] = Field(
        None, description="Minimum seconds between emails"
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

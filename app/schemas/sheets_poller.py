"""Pydantic schemas for the Google Sheets poller feature."""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Lead JSON validation (what teammates paste into Column A)
# ---------------------------------------------------------------------------

class SheetStepEmail(BaseModel):
    """One email step as pasted in the sheet JSON."""
    step_number: int = Field(..., ge=1, le=10)
    subject: str = Field(..., min_length=1)
    body: str = Field(..., min_length=1, description="HTML email body")


class SheetLeadJSON(BaseModel):
    """
    Schema for the JSON that teammates paste into Column A.

    Required fields: campaign_id, email, emails.
    Optional fields: first_name, last_name, company_name.

    The number of emails must match the campaign's sequence count
    (validated at runtime against Smartlead, not hardcoded here).
    """
    campaign_id: int | str = Field(..., description="Smartlead campaign ID")
    email: str = Field(..., description="Lead email address")
    emails: list[SheetStepEmail] = Field(
        ...,
        min_length=1,
        max_length=10,
        description="Email steps — count must match the campaign's sequence count",
    )
    first_name: str = ""
    last_name: str = ""
    company_name: str = ""


# ---------------------------------------------------------------------------
# API request / response models
# ---------------------------------------------------------------------------

class PollerStartRequest(BaseModel):
    """Request body to start a sheet poller."""
    spreadsheet_id: str = Field(
        ...,
        min_length=1,
        description="Google Sheets spreadsheet ID (from the URL).",
        examples=["1umOtK_TRT8xgHNM_M0ifVa0HaOIWj5C38D3u-3h7B40"],
    )
    sheet_name: str = Field(
        default="Sheet1",
        description="Name of the worksheet tab to poll.",
        examples=["Sheet1", "Leads"],
    )
    poll_interval_seconds: int = Field(
        default=60,
        ge=10,
        le=600,
        description="How often to check for new rows (seconds).",
    )


class PollerInfo(BaseModel):
    """Information about a running poller."""
    poller_id: str = Field(..., description="Unique ID for this poller instance.")
    spreadsheet_id: str
    sheet_name: str
    poll_interval_seconds: int
    status: str = Field(..., description="running | stopped")
    started_at: datetime
    rows_processed: int = 0
    rows_succeeded: int = 0
    rows_failed: int = 0
    last_poll_at: Optional[datetime] = None
    last_error: Optional[str] = None


class PollerStartResponse(BaseModel):
    """Response after starting a poller."""
    message: str
    poller: PollerInfo


class PollerStopResponse(BaseModel):
    """Response after stopping a poller."""
    message: str
    poller: PollerInfo


class PollerListResponse(BaseModel):
    """Response listing all active pollers."""
    total: int
    pollers: list[PollerInfo]
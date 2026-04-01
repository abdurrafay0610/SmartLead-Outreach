import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models import InternalCampaign, CampaignDelivery, CampaignLeadLink
from app.schemas.campaign import (
    CampaignCreateRequest,
    CampaignResponse,
    CampaignStatusRequest,
    CampaignSettingsRequest,
    CampaignDetailResponse,
    AssignSenderRequest,
    SequenceSetupRequest,
)
from app.services.campaign_service import CampaignService

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


@router.post("", response_model=CampaignResponse, status_code=201)
async def create_campaign(
    request: CampaignCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Create a new campaign.

    Creates in both internal DB and Smartlead, stores the provider mapping.

    The `num_emails_per_lead` field (default: 1) sets how many sequence
    steps this campaign will have. When you later inject leads, each lead
    must provide exactly this many emails.

    Example for a 3-email campaign:
    ```json
    {
      "name": "Q3 Healthcare Outreach",
      "num_emails_per_lead": 3
    }
    ```
    """
    service = CampaignService(db)
    result = await service.create_campaign_with_sync(
        name=request.name,
        persona=request.persona,
        segment=request.segment,
        num_emails_per_lead=request.num_emails_per_lead,
    )
    campaign = result["campaign"]
    delivery = result["delivery"]

    if result["smartlead_error"]:
        return CampaignResponse(
            id=campaign.id,
            name=campaign.name,
            persona=campaign.persona,
            segment=campaign.segment,
            status=campaign.status,
            num_emails_per_lead=campaign.num_emails_per_lead,
            provider_campaign_id=None,
            created_at=campaign.created_at,
            updated_at=campaign.updated_at,
        )

    return CampaignResponse(
        id=campaign.id,
        name=campaign.name,
        persona=campaign.persona,
        segment=campaign.segment,
        status=campaign.status,
        num_emails_per_lead=campaign.num_emails_per_lead,
        provider_campaign_id=delivery.provider_campaign_id,
        created_at=campaign.created_at,
        updated_at=campaign.updated_at,
    )


@router.get("/sender-accounts/list")
async def list_sender_accounts(
    db: AsyncSession = Depends(get_db),
):
    """
    List all sender email accounts from Smartlead.
    Use the 'id' field from the returned accounts when calling
    POST /campaigns/{id}/sender to link an account to a campaign.
    """
    service = CampaignService(db)
    try:
        accounts = await service.list_sender_accounts()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch from Smartlead: {e}")
    return {"accounts": accounts}


@router.post("/{campaign_id}/sender")
async def assign_sender_to_campaign(
    campaign_id: uuid.UUID,
    request: AssignSenderRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Assign sender email account(s) to a campaign on Smartlead.

    **This is REQUIRED before starting a campaign.** Smartlead will not
    send emails unless at least one sender account is linked.

    Steps:
    1. Call GET /api/v1/campaigns/sender-accounts/list to see available accounts
    2. Pick the account ID(s) you want to use
    3. Call this endpoint with those IDs
    4. Then you can start the campaign with POST /campaigns/{id}/status

    You can assign multiple sender accounts for rotation (recommended for
    better deliverability).
    """
    service = CampaignService(db)
    try:
        result = await service.assign_sender_account(
            campaign_id=campaign_id,
            email_account_ids=request.email_account_ids,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if result.get("smartlead_error"):
        raise HTTPException(
            status_code=502,
            detail=f"Failed to link sender on Smartlead: {result['smartlead_error']}",
        )

    return result


@router.get("/{campaign_id}", response_model=CampaignDetailResponse)
async def get_campaign(
    campaign_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get campaign details with aggregate stats."""
    result = await db.execute(
        select(InternalCampaign).where(InternalCampaign.id == campaign_id)
    )
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    delivery_result = await db.execute(
        select(CampaignDelivery).where(
            CampaignDelivery.internal_campaign_id == campaign_id
        )
    )
    delivery = delivery_result.scalar_one_or_none()

    total_leads = 0
    if delivery:
        lead_count_result = await db.execute(
            select(func.count(CampaignLeadLink.id)).where(
                CampaignLeadLink.campaign_delivery_id == delivery.id
            )
        )
        total_leads = lead_count_result.scalar() or 0

    return CampaignDetailResponse(
        id=campaign.id,
        name=campaign.name,
        persona=campaign.persona,
        segment=campaign.segment,
        status=campaign.status,
        num_emails_per_lead=campaign.num_emails_per_lead,
        provider_campaign_id=delivery.provider_campaign_id if delivery else None,
        total_leads=total_leads,
        created_at=campaign.created_at,
        updated_at=campaign.updated_at,
    )


@router.get("", response_model=list[CampaignResponse])
async def list_campaigns(
    db: AsyncSession = Depends(get_db),
):
    """List all internal campaigns."""
    result = await db.execute(
        select(InternalCampaign).order_by(InternalCampaign.created_at.desc())
    )
    campaigns = result.scalars().all()

    responses = []
    for c in campaigns:
        delivery_result = await db.execute(
            select(CampaignDelivery).where(
                CampaignDelivery.internal_campaign_id == c.id
            )
        )
        delivery = delivery_result.scalar_one_or_none()
        responses.append(
            CampaignResponse(
                id=c.id,
                name=c.name,
                persona=c.persona,
                segment=c.segment,
                status=c.status,
                num_emails_per_lead=c.num_emails_per_lead,
                provider_campaign_id=delivery.provider_campaign_id if delivery else None,
                created_at=c.created_at,
                updated_at=c.updated_at,
            )
        )
    return responses


@router.post("/{campaign_id}/status")
async def update_campaign_status(
    campaign_id: uuid.UUID,
    request: CampaignStatusRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Update campaign status (start/pause/stop).
    Syncs to Smartlead automatically.

    NOTE: Before starting, make sure you have:
    1. Set up sequences (POST /campaigns/{id}/sequences)
    2. Added leads (POST /campaigns/{id}/leads)
    3. Assigned a sender account (POST /campaigns/{id}/sender)
    4. Configured schedule (POST /campaigns/{id}/settings)
    """
    service = CampaignService(db)
    try:
        result = await service.update_status_with_sync(campaign_id, request.status)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return result


@router.post("/{campaign_id}/settings")
async def update_campaign_settings(
    campaign_id: uuid.UUID,
    request: CampaignSettingsRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Update campaign settings (schedule, sender, limits).
    Syncs schedule to Smartlead.
    """
    service = CampaignService(db)
    try:
        result = await service.configure_campaign(
            campaign_id=campaign_id,
            schedule=request.schedule,
            sender_account_id=request.sender_account_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "id": str(campaign_id),
        "message": "Settings updated",
        **result,
    }


@router.post("/{campaign_id}/sequences")
async def setup_sequences(
    campaign_id: uuid.UUID,
    request: SequenceSetupRequest | None = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Set up sequence templates on Smartlead for multi-step campaigns.

    Creates N sequence steps (where N = num_emails_per_lead from campaign creation).
    Each step uses numbered placeholders: {{email_subject_1}}, {{email_body_1}}, etc.
    These get filled from each lead's custom_fields when Smartlead sends.

    **Delay configuration (optional):**
    You can specify delays between steps. If omitted, defaults are used:
    - Step 1: 0 days (send immediately)
    - Step 2: 3 days
    - Step 3+: 5 days

    Example request body for a 3-email campaign with custom delays:
    ```json
    {
      "step_delays": [
        {"step_number": 1, "delay_in_days": 0},
        {"step_number": 2, "delay_in_days": 3},
        {"step_number": 3, "delay_in_days": 7}
      ]
    }
    ```

    Or call with no body to use default delays.
    """
    service = CampaignService(db)
    try:
        step_delays = request.step_delays if request else None
        result = await service.setup_sequences(
            campaign_id=campaign_id,
            step_delays=step_delays,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "message": "Sequences configured",
        "num_steps": result["num_steps"],
        "step_delays": result["step_delays"],
        "smartlead_response": result["smartlead_response"],
    }
import uuid

from fastapi import APIRouter, Depends, HTTPException
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
    """
    service = CampaignService(db)
    result = await service.create_campaign_with_sync(
        name=request.name,
        persona=request.persona,
        segment=request.segment,
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
    Use this to find the sender account to assign to a campaign.
    """
    service = CampaignService(db)
    try:
        accounts = await service.list_sender_accounts()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch from Smartlead: {e}")
    return {"accounts": accounts}


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
    db: AsyncSession = Depends(get_db),
):
    """
    Set up sequence templates on Smartlead.
    Uses {{email_subject}} and {{email_body}} placeholders,
    which get filled from each lead's custom_fields.
    """
    service = CampaignService(db)
    try:
        result = await service.setup_sequences(campaign_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"message": "Sequences configured", "smartlead_response": result}

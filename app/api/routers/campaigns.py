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

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


@router.post("", response_model=CampaignResponse, status_code=201)
async def create_campaign(
    request: CampaignCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create a new internal campaign. Smartlead campaign is created in Phase 2+."""
    campaign = InternalCampaign(
        name=request.name,
        persona=request.persona,
        segment=request.segment,
        status="drafted",
    )
    db.add(campaign)
    await db.flush()

    # Create a delivery record (Smartlead mapping — provider_campaign_id filled in Phase 2)
    delivery = CampaignDelivery(
        internal_campaign_id=campaign.id,
        provider="smartlead",
        status="created",
    )
    db.add(delivery)
    await db.flush()

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

    # Get delivery for provider_campaign_id
    delivery_result = await db.execute(
        select(CampaignDelivery).where(
            CampaignDelivery.internal_campaign_id == campaign_id
        )
    )
    delivery = delivery_result.scalar_one_or_none()

    # Count leads
    lead_count_result = await db.execute(
        select(func.count(CampaignLeadLink.id)).where(
            CampaignLeadLink.campaign_delivery_id == delivery.id
        )
    ) if delivery else None
    total_leads = lead_count_result.scalar() if lead_count_result else 0

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
    """Update campaign status (start/pause/stop). Smartlead sync in Phase 2+."""
    result = await db.execute(
        select(InternalCampaign).where(InternalCampaign.id == campaign_id)
    )
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    # Map our verbs to internal status
    status_map = {"start": "active", "pause": "paused", "stop": "stopped"}
    campaign.status = status_map[request.status]
    await db.flush()

    return {"id": str(campaign.id), "status": campaign.status, "message": f"Campaign {request.status}ed"}


@router.post("/{campaign_id}/settings")
async def update_campaign_settings(
    campaign_id: uuid.UUID,
    request: CampaignSettingsRequest,
    db: AsyncSession = Depends(get_db),
):
    """Update campaign settings (schedule, sender, limits). Smartlead sync in Phase 2+."""
    result = await db.execute(
        select(InternalCampaign).where(InternalCampaign.id == campaign_id)
    )
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    # Update sender account on delivery if provided
    if request.sender_account_id:
        delivery_result = await db.execute(
            select(CampaignDelivery).where(
                CampaignDelivery.internal_campaign_id == campaign_id
            )
        )
        delivery = delivery_result.scalar_one_or_none()
        if delivery:
            delivery.sender_account_id = request.sender_account_id

    # Schedule and other settings will be synced to Smartlead in Phase 2
    return {
        "id": str(campaign.id),
        "message": "Settings updated (Smartlead sync pending Phase 2)",
        "schedule": request.schedule.model_dump() if request.schedule else None,
    }

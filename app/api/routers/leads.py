import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models import (
    CampaignDelivery,
    CampaignLeadLink,
    InternalCampaign,
    Lead,
    OutboundMessage,
)
from app.schemas.lead import LeadInjectRequest, LeadInjectResponse

router = APIRouter(prefix="/campaigns/{campaign_id}/leads", tags=["leads"])


@router.post("", response_model=LeadInjectResponse, status_code=201)
async def inject_leads(
    campaign_id: uuid.UUID,
    request: LeadInjectRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Inject leads with pre-generated email content into a campaign.

    Each lead entry contains (email, subject, body_html). The system:
    1. Upserts the lead record
    2. Creates the campaign-lead link
    3. Stores the immutable outbound_message snapshot
    4. (Phase 3+) Pushes to Smartlead in batches via background worker
    """
    # Verify campaign exists
    campaign_result = await db.execute(
        select(InternalCampaign).where(InternalCampaign.id == campaign_id)
    )
    campaign = campaign_result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    # Get the delivery record for this campaign
    delivery_result = await db.execute(
        select(CampaignDelivery).where(
            CampaignDelivery.internal_campaign_id == campaign_id
        )
    )
    delivery = delivery_result.scalar_one_or_none()
    if not delivery:
        raise HTTPException(status_code=404, detail="Campaign delivery record not found")

    created_count = 0
    skipped_count = 0

    for lead_input in request.leads:
        email_lower = lead_input.email.lower()

        # Upsert lead: find existing or create new
        existing_lead_result = await db.execute(
            select(Lead).where(Lead.email == email_lower)
        )
        lead = existing_lead_result.scalar_one_or_none()

        if not lead:
            lead = Lead(
                email=email_lower,
                first_name=lead_input.first_name,
                last_name=lead_input.last_name,
                company=lead_input.company,
                linkedin_url=lead_input.linkedin_url,
            )
            db.add(lead)
            await db.flush()

        # Check if lead is already linked to this delivery (dedupe)
        existing_link_result = await db.execute(
            select(CampaignLeadLink).where(
                CampaignLeadLink.campaign_delivery_id == delivery.id,
                CampaignLeadLink.lead_id == lead.id,
            )
        )
        existing_link = existing_link_result.scalar_one_or_none()

        if existing_link:
            skipped_count += 1
            continue

        # Create campaign-lead link
        link = CampaignLeadLink(
            campaign_delivery_id=delivery.id,
            lead_id=lead.id,
            status="pending",
        )
        db.add(link)
        await db.flush()

        # Store the immutable email snapshot
        message = OutboundMessage(
            campaign_link_id=link.id,
            step_number=1,
            subject=lead_input.subject,
            body_html=lead_input.body_html,
            body_text=lead_input.body_text,
            prompt_version=lead_input.prompt_version,
            model_name=lead_input.model_name,
            context_snapshot=lead_input.context_snapshot,
            message_status="pending",
            generated_at=datetime.now(timezone.utc),
        )
        db.add(message)
        created_count += 1

    await db.flush()

    return LeadInjectResponse(
        campaign_id=campaign_id,
        total_received=len(request.leads),
        total_created=created_count,
        total_skipped_duplicate=skipped_count,
        message=f"{created_count} leads injected, {skipped_count} duplicates skipped. "
        "Smartlead push will be handled by background worker (Phase 3).",
    )

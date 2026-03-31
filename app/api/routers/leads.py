import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.lead import LeadInjectRequest, LeadInjectResponse
from app.services.campaign_service import CampaignService

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
    1. Upserts the lead record in DB
    2. Creates the campaign-lead link
    3. Stores the immutable outbound_message snapshot
    4. Pushes leads to Smartlead in batches of 400
    5. Stores provider_lead_id mappings

    The subject and body are passed to Smartlead as custom_fields
    ({{email_subject}} and {{email_body}}) so each lead gets their
    unique personalized content.
    """
    service = CampaignService(db)
    try:
        result = await service.inject_leads_with_sync(
            campaign_id=campaign_id,
            leads_input=request.leads,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    smartlead_note = ""
    if result.get("smartlead_error"):
        smartlead_note = f" Smartlead sync error: {result['smartlead_error']}"

    return LeadInjectResponse(
        campaign_id=campaign_id,
        total_received=result["total_received"],
        total_created=result["total_created"],
        total_skipped_duplicate=result["total_skipped_duplicate"],
        message=f"{result['total_created']} leads injected, "
        f"{result['total_skipped_duplicate']} duplicates skipped. "
        f"{result['total_pushed_to_smartlead']} pushed to Smartlead.{smartlead_note}",
    )

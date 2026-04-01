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

    Each lead must provide emails for ALL sequence steps defined at
    campaign creation (num_emails_per_lead).

    **Single-email campaign (num_emails_per_lead=1):**
    ```json
    {
      "leads": [
        {
          "email": "john@acme.com",
          "first_name": "John",
          "company": "Acme Corp",
          "emails": [
            {
              "step_number": 1,
              "subject": "Quick question about Acme",
              "body_html": "<p>Hi John, worth a quick chat?</p>"
            }
          ]
        }
      ]
    }
    ```

    **Multi-email campaign (num_emails_per_lead=3):**
    ```json
    {
      "leads": [
        {
          "email": "john@acme.com",
          "first_name": "John",
          "company": "Acme Corp",
          "emails": [
            {
              "step_number": 1,
              "subject": "Quick question about Acme",
              "body_html": "<p>Hi John, noticed Acme is scaling...</p>"
            },
            {
              "step_number": 2,
              "subject": "Following up, John",
              "body_html": "<p>Hi John, just checking back in...</p>"
            },
            {
              "step_number": 3,
              "subject": "Last note from me",
              "body_html": "<p>Hi John, one final thought...</p>"
            }
          ]
        }
      ]
    }
    ```

    The system:
    1. Validates each lead has exactly num_emails_per_lead emails
    2. Upserts the lead record in DB
    3. Creates the campaign-lead link
    4. Stores immutable outbound_message snapshots (one per step)
    5. Pushes leads to Smartlead in batches of 400
    6. Stores provider_lead_id mappings

    Each step's subject and body are passed to Smartlead as numbered
    custom_fields (email_subject_1, email_body_1, email_subject_2, etc.)
    which match the sequence templates set up in POST /campaigns/{id}/sequences.
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
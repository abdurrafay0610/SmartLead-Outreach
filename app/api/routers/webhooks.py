from fastapi import APIRouter, Request

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/smartlead")
async def receive_smartlead_webhook(request: Request):
    """
    Receive Smartlead webhook events.
    Full implementation in Phase 4.

    Expected events: EMAIL_SENT, EMAIL_OPENED, EMAIL_CLICKED,
    EMAIL_REPLIED, EMAIL_BOUNCED, LEAD_UNSUBSCRIBED
    (naming may vary — see Smart_Lead_Deep_Research.md)
    """
    return {"status": "ok", "message": "Webhook endpoint ready — processing in Phase 4"}

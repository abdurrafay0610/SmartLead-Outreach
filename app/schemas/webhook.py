import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel


class WebhookReceiptResponse(BaseModel):
    id: uuid.UUID
    provider: str
    processing_status: str
    dedupe_key: Optional[str] = None
    error_message: Optional[str] = None
    received_at: datetime
    payload_json: Optional[dict[str, Any]] = None

    model_config = {"from_attributes": True}

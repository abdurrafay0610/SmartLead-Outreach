import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class WebhookReceipt(Base):
    __tablename__ = "webhook_receipts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    provider: Mapped[str] = mapped_column(
        String(50), nullable=False, default="smartlead", server_default="smartlead"
    )
    payload_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    headers_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    dedupe_key: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, index=True)
    processing_status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="received", server_default="received"
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

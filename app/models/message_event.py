import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.outbound_message import OutboundMessage


class MessageEvent(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "message_events"

    outbound_message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("outbound_messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    provider_event_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    event_time: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    payload_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    outbound_message: Mapped["OutboundMessage"] = relationship(back_populates="events")

    __table_args__ = (
        Index("ix_message_events_outbound_message_id", "outbound_message_id"),
        Index("ix_message_events_event_type", "event_type"),
        Index("ix_message_events_event_time", "event_time"),
        # Dedupe: prevent exact same event from same provider
        Index(
            "uq_message_events_provider_event",
            "outbound_message_id",
            "provider_event_id",
            unique=True,
            postgresql_where="provider_event_id IS NOT NULL",
        ),
    )

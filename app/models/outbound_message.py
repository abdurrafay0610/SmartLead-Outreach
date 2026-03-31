import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.campaign_lead_link import CampaignLeadLink
    from app.models.message_event import MessageEvent


class OutboundMessage(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "outbound_messages"

    campaign_link_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("campaign_lead_links.id", ondelete="CASCADE"),
        nullable=False,
    )
    step_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # Immutable email content snapshot
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    body_html: Mapped[str] = mapped_column(Text, nullable=False)
    body_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # LLM metadata (optional — for when you track generation provenance)
    prompt_version: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    model_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    context_snapshot: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    # Custom fields sent to Smartlead for this lead
    custom_fields: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    # Provider tracking
    provider_message_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    message_status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="pending", server_default="pending"
    )

    # Timestamps
    generated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    campaign_link: Mapped["CampaignLeadLink"] = relationship(back_populates="outbound_messages")
    events: Mapped[list["MessageEvent"]] = relationship(
        back_populates="outbound_message", lazy="selectin", order_by="MessageEvent.event_time"
    )

    __table_args__ = (
        Index("ix_outbound_messages_campaign_link_id", "campaign_link_id"),
        Index("ix_outbound_messages_provider_message_id", "provider_message_id"),
        Index("ix_outbound_messages_status", "message_status"),
        # Prevent duplicate step per campaign-lead link
        Index(
            "uq_outbound_messages_link_step",
            "campaign_link_id",
            "step_number",
            unique=True,
        ),
    )

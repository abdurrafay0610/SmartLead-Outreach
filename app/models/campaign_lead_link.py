import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.campaign_delivery import CampaignDelivery
    from app.models.lead import Lead
    from app.models.outbound_message import OutboundMessage


class CampaignLeadLink(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "campaign_lead_links"

    campaign_delivery_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("campaign_deliveries.id", ondelete="CASCADE"),
        nullable=False,
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("leads.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider_lead_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="pending", server_default="pending"
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    campaign_delivery: Mapped["CampaignDelivery"] = relationship(back_populates="lead_links")
    lead: Mapped["Lead"] = relationship(back_populates="campaign_links")
    outbound_messages: Mapped[list["OutboundMessage"]] = relationship(
        back_populates="campaign_link", lazy="selectin"
    )

    __table_args__ = (
        Index("ix_campaign_lead_links_lead_id", "lead_id"),
        Index("ix_campaign_lead_links_delivery_id", "campaign_delivery_id"),
        # Prevent duplicate lead-campaign-delivery combos
        Index(
            "uq_campaign_lead_links_delivery_lead",
            "campaign_delivery_id",
            "lead_id",
            unique=True,
        ),
    )

import uuid
from typing import TYPE_CHECKING, Optional

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.campaign_lead_link import CampaignLeadLink
    from app.models.internal_campaign import InternalCampaign


class CampaignDelivery(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "campaign_deliveries"

    internal_campaign_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("internal_campaigns.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(
        String(50), nullable=False, default="smartlead", server_default="smartlead"
    )
    provider_campaign_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    sender_account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sender_accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="created", server_default="created"
    )

    # Relationships
    internal_campaign: Mapped["InternalCampaign"] = relationship(back_populates="deliveries")
    lead_links: Mapped[list["CampaignLeadLink"]] = relationship(
        back_populates="campaign_delivery", lazy="selectin"
    )

    __table_args__ = (
        Index("ix_campaign_deliveries_internal_campaign_id", "internal_campaign_id"),
        Index("ix_campaign_deliveries_provider_campaign_id", "provider_campaign_id"),
    )

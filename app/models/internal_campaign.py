from typing import TYPE_CHECKING, Optional

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.campaign_delivery import CampaignDelivery


class InternalCampaign(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "internal_campaigns"

    name: Mapped[str] = mapped_column(String(500), nullable=False)
    persona: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    segment: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="drafted", server_default="drafted"
    )

    # Number of sequence steps (emails) each lead receives in this campaign.
    # Fixed at campaign creation time. Leads must provide exactly this many emails.
    num_emails_per_lead: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )

    # Relationships
    deliveries: Mapped[list["CampaignDelivery"]] = relationship(
        back_populates="internal_campaign", lazy="selectin"
    )
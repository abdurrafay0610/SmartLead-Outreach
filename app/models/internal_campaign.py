from typing import TYPE_CHECKING, Optional

from sqlalchemy import String, Text
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

    # Relationships
    deliveries: Mapped[list["CampaignDelivery"]] = relationship(
        back_populates="internal_campaign", lazy="selectin"
    )

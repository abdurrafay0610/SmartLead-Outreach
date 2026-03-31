from typing import TYPE_CHECKING, Optional

from sqlalchemy import Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.campaign_lead_link import CampaignLeadLink


class Lead(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "leads"

    email: Mapped[str] = mapped_column(String(320), nullable=False)
    first_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    last_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    company: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    linkedin_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    campaign_links: Mapped[list["CampaignLeadLink"]] = relationship(
        back_populates="lead", lazy="selectin"
    )

    __table_args__ = (
        # CITEXT-like behavior: unique on lowercased email
        Index("ix_leads_email_lower", "email", unique=True, postgresql_using="btree"),
    )

from typing import Optional

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class SenderAccount(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "sender_accounts"

    email: Mapped[str] = mapped_column(String(320), nullable=False)
    provider_account_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    warmup_status: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True, default="not_started"
    )

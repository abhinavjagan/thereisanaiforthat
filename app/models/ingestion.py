from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    started_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    tools_found: Mapped[int] = mapped_column(Integer, default=0)
    tools_new: Mapped[int] = mapped_column(Integer, default=0)
    tools_updated: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[str | None] = mapped_column(Text)

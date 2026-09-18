"""Multi-tenant SQLAlchemy schema for the commercial revenue platform."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from src.paths import DEFAULT_COOLDOWN_DAYS


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_id() -> str:
    return uuid4().hex


class Base(DeclarativeBase):
    pass


class Organization(Base):
    __tablename__ = "organizations"

    org_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    api_key: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    plan_tier: Mapped[str] = mapped_column(String(32), default="growth", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    slack_webhook_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    stripe_webhook_secret: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    accounts: Mapped[list["CustomerAccount"]] = relationship(back_populates="organization")


class CustomerAccount(Base):
    __tablename__ = "customer_accounts"
    __table_args__ = (
        UniqueConstraint("org_id", "customer_external_id", name="uq_org_external_customer"),
        Index("ix_accounts_org", "org_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.org_id", ondelete="CASCADE"), nullable=False
    )
    customer_external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    channel: Mapped[str] = mapped_column(String(64), default="Paid Search", nullable=False)
    mrr: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    contract_type: Mapped[str] = mapped_column(String(16), default="Monthly", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    last_contacted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    cooldown_days: Mapped[int] = mapped_column(Integer, default=DEFAULT_COOLDOWN_DAYS, nullable=False)
    suppressed_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    contract_renewal_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    approval_status: Mapped[str] = mapped_column(String(32), default="none", nullable=False)

    organization: Mapped[Organization] = relationship(back_populates="accounts")
    telemetry: Mapped[list["TelemetryEvent"]] = relationship(back_populates="account")
    assessments: Mapped[list["ChurnAssessment"]] = relationship(back_populates="account")
    actions: Mapped[list["DispatchedAction"]] = relationship(back_populates="account")
    outcomes: Mapped[list["InterventionOutcome"]] = relationship(back_populates="account")


class TelemetryEvent(Base):
    __tablename__ = "telemetry_events"
    __table_args__ = (Index("ix_telemetry_org_account", "org_id", "customer_account_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.org_id", ondelete="CASCADE"), nullable=False
    )
    customer_account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("customer_accounts.id", ondelete="CASCADE"), nullable=False
    )
    event_name: Mapped[str] = mapped_column(String(80), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    properties: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, default=dict)

    account: Mapped[CustomerAccount] = relationship(back_populates="telemetry")


class ChurnAssessment(Base):
    __tablename__ = "churn_assessments"
    __table_args__ = (Index("ix_assessments_org_account", "org_id", "customer_account_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.org_id", ondelete="CASCADE"), nullable=False
    )
    customer_account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("customer_accounts.id", ondelete="CASCADE"), nullable=False
    )
    churn_probability: Mapped[float] = mapped_column(Float, nullable=False)
    risk_tier: Mapped[str] = mapped_column(String(16), nullable=False)
    arr_at_risk: Mapped[float] = mapped_column(Float, nullable=False)
    recommended_action: Mapped[str] = mapped_column(Text, nullable=False)
    assessed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    account: Mapped[CustomerAccount] = relationship(back_populates="assessments")


class DispatchedAction(Base):
    __tablename__ = "dispatched_actions"
    __table_args__ = (Index("ix_actions_org_created", "org_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.org_id", ondelete="CASCADE"), nullable=False
    )
    customer_account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("customer_accounts.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="simulated", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    account: Mapped[CustomerAccount] = relationship(back_populates="actions")
    outcomes: Mapped[list["InterventionOutcome"]] = relationship(back_populates="action")


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"
    __table_args__ = (Index("ix_jobs_org_status", "org_id", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.org_id", ondelete="CASCADE"), nullable=False
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(24), default="queued", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class InterventionOutcome(Base):
    __tablename__ = "intervention_outcomes"
    __table_args__ = (Index("ix_outcomes_org_account", "org_id", "customer_account_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.org_id", ondelete="CASCADE"), nullable=False
    )
    customer_account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("customer_accounts.id", ondelete="CASCADE"), nullable=False
    )
    dispatched_action_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("dispatched_actions.id", ondelete="SET NULL"), nullable=True
    )
    intervention_date: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    initial_mrr: Mapped[float] = mapped_column(Float, nullable=False)
    status_at_30d: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    status_at_60d: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    status_at_90d: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    verified_arr_saved: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    attributed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    account: Mapped[CustomerAccount] = relationship(back_populates="outcomes")
    action: Mapped[Optional[DispatchedAction]] = relationship(back_populates="outcomes")

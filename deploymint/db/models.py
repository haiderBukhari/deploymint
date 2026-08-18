"""SQLAlchemy 2.0 models. See docs/03-data-model.md."""

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    repo_path: Mapped[str] = mapped_column(String(1024))
    language: Mapped[str | None] = mapped_column(String(50))
    framework: Mapped[str | None] = mapped_column(String(50))
    entrypoint: Mapped[str | None] = mapped_column(String(255))
    exposed_port: Mapped[int] = mapped_column(Integer, default=8000)
    # aws | gcp | azure — which cloud's Terraform module (ECR/EKS, Artifact
    # Registry/GKE, ACR/AKS) gets generated. See docs/19-managed-clusters.md.
    cloud_provider: Mapped[str] = mapped_column(String(20), default="aws")
    analysis: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    runs: Mapped[list["Run"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    current_node: Mapped[str | None] = mapped_column(String(50))
    trigger: Mapped[str] = mapped_column(String(20), default="api")
    force: Mapped[bool] = mapped_column(Boolean, default=False)

    analysis: Mapped[dict | None] = mapped_column(JSONB)
    artifacts: Mapped[dict | None] = mapped_column(JSONB)
    security: Mapped[dict | None] = mapped_column(JSONB)
    deployment: Mapped[dict | None] = mapped_column(JSONB)
    cost: Mapped[dict | None] = mapped_column(JSONB)
    errors: Mapped[list | None] = mapped_column(JSONB, default=list)

    model_used: Mapped[str | None] = mapped_column(String(100))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # A one-click "terraform apply" against a real cloud account, run on demand
    # well after the pipeline finishes — see docs/21-cloud-deploy.md. Never
    # stores credentials; only the plan/apply outcome and its console output.
    cloud_deploy_status: Mapped[str | None] = mapped_column(String(20))  # running|success|failed
    cloud_deploy_action: Mapped[str | None] = mapped_column(String(20))  # plan|apply|destroy
    cloud_deploy_output: Mapped[str | None] = mapped_column(Text)
    cloud_deployed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    project: Mapped[Project] = relationship(back_populates="runs")

    __table_args__ = (Index("ix_runs_project_created", "project_id", "created_at"),)


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(50))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (Index("ix_events_run_seq", "run_id", "seq", unique=True),)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    agent: Mapped[str] = mapped_column(String(50))
    action: Mapped[str] = mapped_column(String(50))
    command: Mapped[str] = mapped_column(Text)
    output: Mapped[str] = mapped_column(Text, default="")
    exit_code: Mapped[int | None] = mapped_column(Integer)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (Index("ix_audit_run_seq", "run_id", "seq", unique=True),)

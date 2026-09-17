from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Brand(TimestampMixin, Base):
    __tablename__ = "brands"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    sub_brand: Mapped[str | None] = mapped_column(String(80))
    official_url: Mapped[str | None] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    phones: Mapped[list[PhoneModel]] = relationship(back_populates="brand")


class PhoneModel(TimestampMixin, Base):
    __tablename__ = "phone_models"
    __table_args__ = (
        UniqueConstraint("brand_id", "model_name", name="uq_phone_brand_model"),
        Index("idx_phone_release_status", "release_date", "sale_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("brands.id", ondelete="RESTRICT"), index=True)
    model_name: Mapped[str] = mapped_column(String(160), index=True)
    release_date: Mapped[date | None] = mapped_column(Date, index=True)
    sale_status: Mapped[str] = mapped_column(String(30), default="on_sale", index=True)
    cpu: Mapped[str | None] = mapped_column(String(160))
    screen_size: Mapped[float | None] = mapped_column(Float)
    screen_type: Mapped[str | None] = mapped_column(String(120))
    resolution: Mapped[str | None] = mapped_column(String(80))
    refresh_rate: Mapped[int | None] = mapped_column(Integer)
    main_camera_mp: Mapped[float | None] = mapped_column(Float)
    camera_summary: Mapped[str | None] = mapped_column(Text)
    battery_mah: Mapped[int | None] = mapped_column(Integer)
    charging_w: Mapped[float | None] = mapped_column(Float)
    wireless_charging_w: Mapped[float | None] = mapped_column(Float)
    wireless_charging_supported: Mapped[bool | None] = mapped_column(Boolean)
    weight_g: Mapped[float | None] = mapped_column(Float)
    thickness_mm: Mapped[float | None] = mapped_column(Float)
    waterproof: Mapped[str | None] = mapped_column(String(50))
    waterproof_supported: Mapped[bool | None] = mapped_column(Boolean)
    nfc: Mapped[bool | None] = mapped_column(Boolean)
    five_g: Mapped[bool | None] = mapped_column(Boolean)
    screen_shape: Mapped[str | None] = mapped_column(String(30))
    telephoto: Mapped[bool | None] = mapped_column(Boolean)
    operating_system: Mapped[str | None] = mapped_column(String(100))
    image_url: Mapped[str | None] = mapped_column(String(1000))
    image_source_url: Mapped[str | None] = mapped_column(String(500))
    official_url: Mapped[str | None] = mapped_column(String(500))
    source_url: Mapped[str | None] = mapped_column(String(500))
    source_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data_quality: Mapped[str] = mapped_column(String(30), default="unverified")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    brand: Mapped[Brand] = relationship(back_populates="phones")
    variants: Mapped[list[PhoneVariant]] = relationship(
        back_populates="model", cascade="all, delete-orphan"
    )


class PhoneVariant(TimestampMixin, Base):
    __tablename__ = "phone_variants"
    __table_args__ = (
        UniqueConstraint(
            "model_id", "ram_gb", "storage_gb", name="uq_variant_model_memory"
        ),
        Index("idx_variant_active_storage", "is_active", "storage_gb"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    model_id: Mapped[int] = mapped_column(
        ForeignKey("phone_models.id", ondelete="CASCADE"), index=True
    )
    ram_gb: Mapped[int | None] = mapped_column(Integer)
    storage_gb: Mapped[int] = mapped_column(Integer)
    variant_name: Mapped[str] = mapped_column(String(100))
    launch_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), index=True)
    launch_price_source: Mapped[str | None] = mapped_column(String(500))
    color_limited: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    model: Mapped[PhoneModel] = relationship(back_populates="variants")
    listings: Mapped[list[PlatformListing]] = relationship(
        back_populates="variant", cascade="all, delete-orphan"
    )


class PlatformListing(TimestampMixin, Base):
    __tablename__ = "platform_listings"
    __table_args__ = (
        UniqueConstraint(
            "platform", "external_id", "sku_text", name="uq_listing_platform_sku"
        ),
        Index("idx_listing_platform_active", "platform", "is_active"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    variant_id: Mapped[int] = mapped_column(
        ForeignKey("phone_variants.id", ondelete="CASCADE"), index=True
    )
    platform: Mapped[str] = mapped_column(String(20), index=True)
    store_name: Mapped[str] = mapped_column(String(160))
    store_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    external_id: Mapped[str] = mapped_column(String(120))
    sku_text: Mapped[str] = mapped_column(String(160))
    product_title: Mapped[str | None] = mapped_column(String(500))
    product_url: Mapped[str] = mapped_column(String(1000))
    region: Mapped[str] = mapped_column(String(80), default="中国大陆")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    variant: Mapped[PhoneVariant] = relationship(back_populates="listings")
    prices: Mapped[list[PriceSnapshot]] = relationship(
        back_populates="listing", cascade="all, delete-orphan"
    )


class PriceSnapshot(Base):
    __tablename__ = "price_snapshots"
    __table_args__ = (
        Index("idx_price_listing_time", "listing_id", "crawled_at"),
        Index("idx_price_stock_time", "in_stock", "crawled_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[int] = mapped_column(
        ForeignKey("platform_listings.id", ondelete="CASCADE"), index=True
    )
    regular_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    public_sale_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    displayed_gov_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    estimated_gov_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    billion_subsidy_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    promotion_labels: Mapped[str | None] = mapped_column(Text)
    promotion_stackable: Mapped[str] = mapped_column(String(20), default="unknown")
    in_stock: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    crawl_status: Mapped[str] = mapped_column(String(40), default="success")
    evidence_text_path: Mapped[str | None] = mapped_column(String(500))
    screenshot_path: Mapped[str | None] = mapped_column(String(500))
    crawled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )

    listing: Mapped[PlatformListing] = relationship(back_populates="prices")


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(500))
    role: Mapped[str] = mapped_column(String(30), default="admin", index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("idx_audit_user_time", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(50))
    table_name: Mapped[str] = mapped_column(String(80))
    record_id: Mapped[int | None] = mapped_column(Integer)
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    ip_address: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RecommendationRun(Base):
    __tablename__ = "recommendation_runs"
    __table_args__ = (Index("idx_recommendation_created", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_query: Mapped[str] = mapped_column(Text)
    parsed_requirements: Mapped[str | None] = mapped_column(Text)
    final_result: Mapped[str | None] = mapped_column(Text)
    total_latency_ms: Mapped[float | None] = mapped_column(Float)
    candidate_count: Mapped[int] = mapped_column(Integer, default=0)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    model_runs: Mapped[list[ModelRun]] = relationship(
        back_populates="recommendation", cascade="all, delete-orphan"
    )


class ModelRun(Base):
    __tablename__ = "model_runs"
    __table_args__ = (Index("idx_model_name_created", "model_name", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    recommendation_id: Mapped[int] = mapped_column(
        ForeignKey("recommendation_runs.id", ondelete="CASCADE"), index=True
    )
    model_name: Mapped[str] = mapped_column(String(120), index=True)
    latency_ms: Mapped[float | None] = mapped_column(Float)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    response_tokens: Mapped[int | None] = mapped_column(Integer)
    tokens_per_second: Mapped[float | None] = mapped_column(Float)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    json_parse_success: Mapped[bool] = mapped_column(Boolean, default=False)
    response_text: Mapped[str | None] = mapped_column(Text)
    parsed_response: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    recommendation: Mapped[RecommendationRun] = relationship(back_populates="model_runs")


class CrawlLog(Base):
    __tablename__ = "crawl_logs"
    __table_args__ = (Index("idx_crawl_name_started", "crawler_name", "started_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    crawler_name: Mapped[str] = mapped_column(String(120))
    platform: Mapped[str | None] = mapped_column(String(30))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_count: Mapped[int] = mapped_column(Integer, default=0)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(30), default="running")
    error_message: Mapped[str | None] = mapped_column(Text)

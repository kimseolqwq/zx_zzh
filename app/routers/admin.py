from __future__ import annotations

import json
import statistics
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.config import BASE_DIR
from app.crawlers.market_import import _store_whitelist, _valid_product_url
from app.database import get_db
from app.models import (
    AuditLog,
    Brand,
    CrawlLog,
    ModelRun,
    PhoneModel,
    PhoneVariant,
    PlatformListing,
    PriceSnapshot,
    RecommendationRun,
    User,
)
from app.security import (
    current_admin,
    ensure_csrf_token,
    login_limiter,
    require_admin,
    verify_csrf,
    verify_password,
)
from app.services.pricing import (
    DISPLAY_TIMEZONE,
    as_aware_utc,
    latest_snapshot,
    snapshot_summary,
)


router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory=BASE_DIR / "app" / "templates")
AUDIT_ACTION_LABELS = {
    "login": "登录",
    "logout": "退出",
    "create": "新增",
    "update": "修改",
    "soft_delete": "停用",
}
AUDIT_TABLE_LABELS = {
    "users": "管理员",
    "phone_models": "手机型号",
    "phone_variants": "内存版本",
    "platform_listings": "平台商品",
    "price_snapshots": "价格快照",
    "brands": "品牌",
}


def _ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _audit(db: Session, user: User, request: Request, action: str, table: str, record_id: int | None, old=None, new=None) -> None:
    db.add(
        AuditLog(
            user_id=user.id,
            action=action,
            table_name=table,
            record_id=record_id,
            old_value=json.dumps(old, ensure_ascii=False, default=str) if old is not None else None,
            new_value=json.dumps(new, ensure_ascii=False, default=str) if new is not None else None,
            ip_address=_ip(request),
        )
    )


def _redirect(path: str = "/admin") -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


def _validate_listing_input(*, brand_name: str, platform: str, store_name: str, product_url: str) -> None:
    platform = platform.strip().lower()
    store_name = store_name.strip()
    product_url = product_url.strip()
    if platform not in {"jd", "tmall", "pdd"}:
        raise HTTPException(422, "平台无效")
    if not _valid_product_url(platform, product_url):
        raise HTTPException(422, "商品链接必须是对应平台的 HTTPS 地址")
    key = (platform, brand_name.strip().casefold(), store_name.casefold())
    if key not in _store_whitelist():
        raise HTTPException(422, "店铺尚未进入‘平台 + 品牌 + 精确店名’官方店白名单")


def _validate_price_relationships(
    regular: Decimal | None,
    public: Decimal | None,
) -> None:
    if regular is not None and public is not None and public > regular:
        raise HTTPException(422, "公开活动价不能高于常规价")


def _optional_float(value: str) -> float | None:
    return float(value) if value.strip() else None


def _optional_int(value: str) -> int | None:
    return int(value) if value.strip() else None


def _optional_bool(value: str) -> bool | None:
    text = value.strip().casefold()
    if not text:
        return None
    if text in {"1", "true", "yes", "支持"}:
        return True
    if text in {"0", "false", "no", "不支持"}:
        return False
    raise HTTPException(422, "布尔参数格式无效")


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: Session = Depends(get_db)):
    if current_admin(request, db):
        return _redirect()
    return templates.TemplateResponse(
        request,
        "admin_login.html",
        {"request": request, "csrf_token": ensure_csrf_token(request), "error": None},
    )


@router.post("/login", response_class=HTMLResponse)
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    key = f"{_ip(request)}:{username.lower()}"
    if not login_limiter.allowed(key):
        return templates.TemplateResponse(
            request,
            "admin_login.html",
            {"request": request, "csrf_token": ensure_csrf_token(request), "error": "尝试次数过多，请5分钟后再试。"},
            status_code=429,
        )
    user = db.scalar(select(User).where(User.username == username))
    if not user or not user.is_active or user.role != "admin" or not verify_password(user.password_hash, password):
        login_limiter.failure(key)
        return templates.TemplateResponse(
            request,
            "admin_login.html",
            {"request": request, "csrf_token": ensure_csrf_token(request), "error": "用户名或密码错误。"},
            status_code=401,
        )
    login_limiter.success(key)
    request.session.clear()
    request.session["user_id"] = user.id
    request.session["csrf_token"] = ensure_csrf_token(request)
    user.last_login_at = datetime.now(timezone.utc)
    _audit(db, user, request, "login", "users", user.id)
    db.commit()
    return _redirect()


@router.post("/logout")
def logout(request: Request, csrf_token: str = Form(...), db: Session = Depends(get_db)):
    user = require_admin(request, db)
    verify_csrf(request, csrf_token)
    _audit(db, user, request, "logout", "users", user.id)
    db.commit()
    request.session.clear()
    return _redirect("/")


@router.get("", response_class=HTMLResponse)
def dashboard(request: Request, q: str = "", db: Session = Depends(get_db)):
    user = current_admin(request, db)
    if not user:
        return _redirect("/admin/login")
    brands = db.scalars(select(Brand).order_by(Brand.name)).all()
    query_text = q.strip()[:80]
    phones = db.scalars(
        select(PhoneModel)
        .options(selectinload(PhoneModel.brand), selectinload(PhoneModel.variants))
        .join(PhoneModel.brand)
        .where(
            or_(
                PhoneModel.model_name.ilike(f"%{query_text}%"),
                Brand.name.ilike(f"%{query_text}%"),
            )
        )
        .order_by(PhoneModel.updated_at.desc())
    ).unique().all()
    phone_ids = [phone.id for phone in phones]
    variants = db.scalars(
        select(PhoneVariant)
        .options(selectinload(PhoneVariant.model).selectinload(PhoneModel.brand))
        .where(PhoneVariant.model_id.in_(phone_ids))
        .order_by(PhoneVariant.id.desc())
    ).unique().all()
    variant_ids = [variant.id for variant in variants]
    listings = db.scalars(
        select(PlatformListing)
        .options(
            selectinload(PlatformListing.variant).selectinload(PhoneVariant.model),
            selectinload(PlatformListing.prices),
        )
        .where(PlatformListing.variant_id.in_(variant_ids))
        .order_by(PlatformListing.updated_at.desc())
    ).unique().all()
    listing_rows = [
        {"listing": listing, "latest": snapshot_summary(latest_snapshot(listing))}
        for listing in listings
    ]
    rec_stats = {
        "runs": db.scalar(select(func.count()).select_from(RecommendationRun)) or 0,
        "model_runs": db.scalar(select(func.count()).select_from(ModelRun)) or 0,
        "crawl_runs": db.scalar(select(func.count()).select_from(CrawlLog)) or 0,
    }
    recent_model_runs = db.scalars(select(ModelRun).order_by(ModelRun.created_at.desc()).limit(100)).all()
    grouped: dict[str, list[ModelRun]] = {}
    for item in recent_model_runs:
        grouped.setdefault(item.model_name, []).append(item)
    model_metrics = []
    for model_name, rows in grouped.items():
        latencies = [row.latency_ms for row in rows if row.latency_ms is not None]
        speeds = [row.tokens_per_second for row in rows if row.tokens_per_second is not None]
        model_metrics.append({
            "model_name": model_name,
            "calls": len(rows),
            "success_rate": round(sum(row.success for row in rows) / len(rows) * 100, 1),
            "json_rate": round(sum(row.json_parse_success for row in rows) / len(rows) * 100, 1),
            "avg_latency_s": round(statistics.mean(latencies) / 1000, 2) if latencies else None,
            "avg_tps": round(statistics.mean(speeds), 2) if speeds else None,
        })
    audit_logs = db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(12)).all()
    return templates.TemplateResponse(
        request,
        "admin_dashboard.html",
        {
            "request": request,
            "user": user,
            "using_default_password": verify_password(user.password_hash, "Admin@123456"),
            "csrf_token": ensure_csrf_token(request),
            "brands": brands,
            "q": query_text,
            "phones": phones,
            "variants": variants,
            "listings": listings,
            "listing_rows": listing_rows,
            "rec_stats": rec_stats,
            "model_metrics": model_metrics,
            "audit_logs": audit_logs,
        },
    )


@router.get("/audit-logs", response_class=HTMLResponse)
def audit_logs(
    request: Request,
    admin_id: str = "",
    page: str = "1",
    db: Session = Depends(get_db),
):
    user = current_admin(request, db)
    if not user:
        return _redirect("/admin/login")

    selected_admin_id: int | None = None
    if admin_id.strip():
        try:
            selected_admin_id = int(admin_id)
        except ValueError:
            selected_admin_id = None
    try:
        current_page = max(1, int(page))
    except ValueError:
        current_page = 1

    per_page = 50
    filters = []
    if selected_admin_id is not None:
        filters.append(AuditLog.user_id == selected_admin_id)
    total = db.scalar(select(func.count()).select_from(AuditLog).where(*filters)) or 0
    total_pages = max(1, (total + per_page - 1) // per_page)
    current_page = min(current_page, total_pages)
    rows = db.execute(
        select(AuditLog, User)
        .outerjoin(User, AuditLog.user_id == User.id)
        .where(*filters)
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(per_page)
        .offset((current_page - 1) * per_page)
    ).all()
    administrators = db.scalars(
        select(User).where(User.role == "admin").order_by(User.username)
    ).all()
    log_rows = [
        {
            "id": log.id,
            "created_at": as_aware_utc(log.created_at).astimezone(DISPLAY_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S"),
            "username": log_user.username if log_user else "已删除管理员",
            "action": AUDIT_ACTION_LABELS.get(log.action, log.action),
            "table_name": AUDIT_TABLE_LABELS.get(log.table_name, log.table_name),
            "record_id": log.record_id,
            "ip_address": log.ip_address,
            "old_value": log.old_value,
            "new_value": log.new_value,
        }
        for log, log_user in rows
    ]
    return templates.TemplateResponse(
        request,
        "admin_audit_logs.html",
        {
            "request": request,
            "user": user,
            "csrf_token": ensure_csrf_token(request),
            "logs": log_rows,
            "administrators": administrators,
            "selected_admin_id": selected_admin_id,
            "page": current_page,
            "total": total,
            "total_pages": total_pages,
            "has_prev": current_page > 1,
            "has_next": current_page < total_pages,
        },
    )


@router.post("/brands")
def create_brand(request: Request, name: str = Form(...), official_url: str = Form(""), csrf_token: str = Form(...), db: Session = Depends(get_db)):
    user = require_admin(request, db); verify_csrf(request, csrf_token)
    if db.scalar(select(Brand).where(Brand.name == name.strip())):
        raise HTTPException(409, "品牌已存在")
    brand = Brand(name=name.strip(), official_url=official_url.strip() or None)
    db.add(brand); db.flush(); _audit(db, user, request, "create", "brands", brand.id, new={"name": brand.name}); db.commit()
    return _redirect("/admin#brands")


@router.post("/brands/{brand_id}/toggle")
def toggle_brand(request: Request, brand_id: int, csrf_token: str = Form(...), db: Session = Depends(get_db)):
    user = require_admin(request, db); verify_csrf(request, csrf_token)
    brand = db.get(Brand, brand_id)
    if not brand: raise HTTPException(404, "品牌不存在")
    old = brand.is_active; brand.is_active = not brand.is_active
    _audit(db, user, request, "update", "brands", brand.id, {"is_active": old}, {"is_active": brand.is_active}); db.commit()
    return _redirect("/admin#brands")


@router.post("/phones")
def create_phone(
    request: Request, brand_id: int = Form(...), model_name: str = Form(...), release_date: str = Form(""),
    cpu: str = Form(""), screen_size: str = Form(""), screen_type: str = Form(""), resolution: str = Form(""),
    refresh_rate: str = Form(""), main_camera_mp: str = Form(""), camera_summary: str = Form(""),
    battery_mah: str = Form(""), charging_w: str = Form(""), wireless_charging_w: str = Form(""),
    wireless_charging_supported: str = Form(""), weight_g: str = Form(""), thickness_mm: str = Form(""),
    waterproof: str = Form(""), waterproof_supported: str = Form(""), nfc: str = Form(""),
    five_g: str = Form(""), screen_shape: str = Form(""), telephoto: str = Form(""),
    operating_system: str = Form(""), official_url: str = Form(""), csrf_token: str = Form(...),
    image_url: str = Form(""), image_source_url: str = Form(""),
    db: Session = Depends(get_db),
):
    user = require_admin(request, db); verify_csrf(request, csrf_token)
    phone = PhoneModel(
        brand_id=brand_id, model_name=model_name.strip(), release_date=date.fromisoformat(release_date) if release_date else None,
        cpu=cpu.strip() or None, screen_size=_optional_float(screen_size), screen_type=screen_type.strip() or None,
        resolution=resolution.strip() or None, refresh_rate=_optional_int(refresh_rate),
        main_camera_mp=_optional_float(main_camera_mp), camera_summary=camera_summary.strip() or None,
        battery_mah=_optional_int(battery_mah), charging_w=_optional_float(charging_w),
        wireless_charging_w=_optional_float(wireless_charging_w),
        wireless_charging_supported=_optional_bool(wireless_charging_supported),
        weight_g=_optional_float(weight_g), thickness_mm=_optional_float(thickness_mm),
        waterproof=waterproof.strip() or None,
        waterproof_supported=_optional_bool(waterproof_supported),
        nfc=_optional_bool(nfc), five_g=_optional_bool(five_g),
        screen_shape=screen_shape.strip() or None, telephoto=_optional_bool(telephoto),
        operating_system=operating_system.strip() or None,
        image_url=image_url.strip() or None, image_source_url=image_source_url.strip() or None,
        official_url=official_url.strip() or None, source_url=official_url.strip() or None,
        source_checked_at=datetime.now(timezone.utc), sale_status="on_sale", data_quality="manual",
    )
    db.add(phone); db.flush(); _audit(db, user, request, "create", "phone_models", phone.id, new={"model_name": phone.model_name}); db.commit()
    return _redirect("/admin#phones")


@router.post("/phones/{phone_id}/update")
def update_phone(
    request: Request, phone_id: int, model_name: str = Form(...), sale_status: str = Form(...), cpu: str = Form(""),
    screen_size: str = Form(""), screen_type: str = Form(""), resolution: str = Form(""),
    refresh_rate: str = Form(""), main_camera_mp: str = Form(""), camera_summary: str = Form(""),
    battery_mah: str = Form(""), charging_w: str = Form(""), wireless_charging_w: str = Form(""),
    wireless_charging_supported: str = Form(""), weight_g: str = Form(""), thickness_mm: str = Form(""),
    waterproof: str = Form(""), waterproof_supported: str = Form(""), nfc: str = Form(""),
    five_g: str = Form(""), screen_shape: str = Form(""), telephoto: str = Form(""),
    operating_system: str = Form(""), image_url: str = Form(""), image_source_url: str = Form(""),
    csrf_token: str = Form(...), db: Session = Depends(get_db),
):
    user = require_admin(request, db); verify_csrf(request, csrf_token)
    phone = db.get(PhoneModel, phone_id)
    if not phone: raise HTTPException(404, "手机不存在")
    old = {
        "model_name": phone.model_name, "sale_status": phone.sale_status, "cpu": phone.cpu,
        "screen_size": phone.screen_size, "screen_type": phone.screen_type, "resolution": phone.resolution,
        "refresh_rate": phone.refresh_rate, "main_camera_mp": phone.main_camera_mp,
        "battery_mah": phone.battery_mah, "charging_w": phone.charging_w,
        "wireless_charging_w": phone.wireless_charging_w, "weight_g": phone.weight_g,
        "thickness_mm": phone.thickness_mm, "waterproof": phone.waterproof,
        "operating_system": phone.operating_system, "image_url": phone.image_url,
    }
    phone.model_name = model_name.strip(); phone.sale_status = sale_status; phone.cpu = cpu.strip() or None
    phone.screen_size = _optional_float(screen_size); phone.screen_type = screen_type.strip() or None
    phone.resolution = resolution.strip() or None; phone.refresh_rate = _optional_int(refresh_rate)
    phone.main_camera_mp = _optional_float(main_camera_mp); phone.camera_summary = camera_summary.strip() or None
    phone.battery_mah = _optional_int(battery_mah); phone.charging_w = _optional_float(charging_w)
    phone.wireless_charging_w = _optional_float(wireless_charging_w)
    phone.wireless_charging_supported = _optional_bool(wireless_charging_supported)
    phone.weight_g = _optional_float(weight_g); phone.thickness_mm = _optional_float(thickness_mm)
    phone.waterproof = waterproof.strip() or None
    phone.waterproof_supported = _optional_bool(waterproof_supported)
    phone.nfc = _optional_bool(nfc); phone.five_g = _optional_bool(five_g)
    phone.screen_shape = screen_shape.strip() or None; phone.telephoto = _optional_bool(telephoto)
    phone.operating_system = operating_system.strip() or None
    phone.image_url = image_url.strip() or None; phone.image_source_url = image_source_url.strip() or None
    _audit(db, user, request, "update", "phone_models", phone.id, old, {"model_name": phone.model_name, "sale_status": phone.sale_status}); db.commit()
    return _redirect("/admin#phones")


@router.post("/phones/{phone_id}/delete")
def delete_phone(request: Request, phone_id: int, csrf_token: str = Form(...), db: Session = Depends(get_db)):
    user = require_admin(request, db); verify_csrf(request, csrf_token)
    phone = db.get(PhoneModel, phone_id)
    if not phone: raise HTTPException(404, "手机不存在")
    phone.is_active = False; phone.sale_status = "discontinued"
    _audit(db, user, request, "soft_delete", "phone_models", phone.id, new={"is_active": False}); db.commit()
    return _redirect("/admin#phones")


@router.post("/variants")
def create_variant(
    request: Request, model_id: int = Form(...), ram_gb: str = Form(""), storage_gb: int = Form(...),
    launch_price: str = Form(""), csrf_token: str = Form(...), db: Session = Depends(get_db),
):
    user = require_admin(request, db); verify_csrf(request, csrf_token)
    ram = int(ram_gb) if ram_gb else None
    variant = PhoneVariant(
        model_id=model_id, ram_gb=ram, storage_gb=storage_gb,
        variant_name=f"{ram}GB+{storage_gb}GB" if ram else f"{storage_gb}GB",
        launch_price=Decimal(launch_price) if launch_price else None, is_active=True,
    )
    db.add(variant); db.flush(); _audit(db, user, request, "create", "phone_variants", variant.id, new={"variant": variant.variant_name}); db.commit()
    return _redirect("/admin#variants")


@router.post("/variants/{variant_id}/delete")
def delete_variant(request: Request, variant_id: int, csrf_token: str = Form(...), db: Session = Depends(get_db)):
    user = require_admin(request, db); verify_csrf(request, csrf_token)
    variant = db.get(PhoneVariant, variant_id)
    if not variant: raise HTTPException(404, "版本不存在")
    variant.is_active = False; _audit(db, user, request, "soft_delete", "phone_variants", variant.id); db.commit()
    return _redirect("/admin#variants")


@router.post("/listings")
def create_listing(
    request: Request, variant_id: int = Form(...), platform: str = Form(...), store_name: str = Form(...),
    external_id: str = Form(...), sku_text: str = Form(...), product_url: str = Form(...), csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    user = require_admin(request, db); verify_csrf(request, csrf_token)
    platform = platform.strip().lower()
    store_name = store_name.strip()
    external_id = external_id.strip()
    sku_text = sku_text.strip()
    product_url = product_url.strip()
    variant = db.scalar(
        select(PhoneVariant)
        .options(selectinload(PhoneVariant.model).selectinload(PhoneModel.brand))
        .where(PhoneVariant.id == variant_id)
    )
    if variant is None or not variant.is_active:
        raise HTTPException(404, "手机版本不存在或已停用")
    _validate_listing_input(
        brand_name=variant.model.brand.name,
        platform=platform,
        store_name=store_name,
        product_url=product_url,
    )
    if not external_id or not sku_text:
        raise HTTPException(422, "商品 ID 和 SKU 文字不能为空")
    duplicate = db.scalar(select(PlatformListing).where(
        PlatformListing.platform == platform,
        PlatformListing.external_id == external_id,
        PlatformListing.sku_text == sku_text,
    ))
    if duplicate is not None:
        raise HTTPException(409, "相同平台、商品 ID 和 SKU 已存在")
    listing = PlatformListing(
        variant_id=variant_id, platform=platform, store_name=store_name, store_verified=True,
        external_id=external_id, sku_text=sku_text, product_url=product_url, region="中国大陆", is_active=True,
    )
    db.add(listing); db.flush(); _audit(db, user, request, "create", "platform_listings", listing.id, new={"platform": platform}); db.commit()
    return _redirect("/admin#listings")


@router.post("/listings/{listing_id}/price")
def add_price(
    request: Request, listing_id: int, regular_price: str = Form(""), public_sale_price: str = Form(""),
    in_stock: bool = Form(False), csrf_token: str = Form(...), db: Session = Depends(get_db),
):
    user = require_admin(request, db); verify_csrf(request, csrf_token)
    listing = db.get(PlatformListing, listing_id)
    if not listing:
        raise HTTPException(404, "商品链接不存在")

    def price(value: str) -> Decimal | None:
        if not value.strip():
            return None
        try:
            parsed = Decimal(value).quantize(Decimal("0.01"))
        except InvalidOperation as exc:
            raise HTTPException(422, "价格格式无效") from exc
        if not Decimal("300") <= parsed <= Decimal("30000"):
            raise HTTPException(422, "价格应在 300 到 30000 元之间")
        return parsed

    regular = price(regular_price)
    public = price(public_sale_price)
    if not any((regular, public)):
        raise HTTPException(422, "至少填写一个价格")
    _validate_price_relationships(regular, public)
    snapshot = PriceSnapshot(
        listing_id=listing_id, regular_price=regular, public_sale_price=public,
        promotion_labels="管理员录入",
        crawl_status="manual", in_stock=in_stock,
    )
    listing.last_checked_at = datetime.now(timezone.utc)
    db.add(snapshot); db.flush()
    _audit(
        db, user, request, "create", "price_snapshots", snapshot.id,
        new={"listing_id": listing_id, "public_sale_price": public, "crawl_status": "manual"},
    )
    db.commit()
    return _redirect("/admin#listings")


@router.post("/listings/{listing_id}/delete")
def delete_listing(request: Request, listing_id: int, csrf_token: str = Form(...), db: Session = Depends(get_db)):
    user = require_admin(request, db); verify_csrf(request, csrf_token)
    listing = db.get(PlatformListing, listing_id)
    if not listing: raise HTTPException(404, "商品链接不存在")
    listing.is_active = False; _audit(db, user, request, "soft_delete", "platform_listings", listing.id); db.commit()
    return _redirect("/admin#listings")

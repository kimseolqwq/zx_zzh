from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.config import BASE_DIR, settings
from app.database import get_db
from app.models import (
    Brand,
    PhoneDislike,
    PhoneLike,
    PhoneModel,
    PhoneVariant,
    PlatformListing,
    PriceSnapshot,
)
from app.services.ollama import OllamaClient
from app.services.evaluation import build_evaluation_metrics
from app.services.recommendation import Requirements, recommend
from app.services.pricing import latest_snapshot, platform_search_url, snapshot_summary


router = APIRouter()
templates = Jinja2Templates(directory=BASE_DIR / "app" / "templates")


class PhoneLikePayload(BaseModel):
    visitor_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    liked: bool


class PhoneDislikePayload(BaseModel):
    visitor_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    disliked: bool


def _like_counts(db: Session, phone_ids: list[int]) -> dict[int, int]:
    if not phone_ids:
        return {}
    rows = db.execute(
        select(PhoneLike.phone_id, func.count(PhoneLike.id))
        .where(PhoneLike.phone_id.in_(set(phone_ids)))
        .group_by(PhoneLike.phone_id)
    ).all()
    return {int(phone_id): int(count) for phone_id, count in rows}


def _dislike_counts(db: Session, phone_ids: list[int]) -> dict[int, int]:
    if not phone_ids:
        return {}
    rows = db.execute(
        select(PhoneDislike.phone_id, func.count(PhoneDislike.id))
        .where(PhoneDislike.phone_id.in_(set(phone_ids)))
        .group_by(PhoneDislike.phone_id)
    ).all()
    return {int(phone_id): int(count) for phone_id, count in rows}


def _attach_like_counts(db: Session, results: list[dict]) -> None:
    phone_ids = [int(item["model_id"]) for item in results if item.get("model_id")]
    likes = _like_counts(db, phone_ids)
    dislikes = _dislike_counts(db, phone_ids)
    for item in results:
        phone_id = int(item["model_id"]) if item.get("model_id") else None
        item["like_count"] = likes.get(phone_id, 0) if phone_id else 0
        item["dislike_count"] = dislikes.get(phone_id, 0) if phone_id else 0


def _stats(db: Session) -> dict[str, int]:
    return {
        "phones": db.scalar(select(func.count()).select_from(PhoneModel).where(PhoneModel.is_active.is_(True))) or 0,
        "variants": db.scalar(select(func.count()).select_from(PhoneVariant).where(PhoneVariant.is_active.is_(True))) or 0,
        "prices": db.scalar(select(func.count()).select_from(PriceSnapshot)) or 0,
        "brands": db.scalar(select(func.count()).select_from(Brand).where(Brand.is_active.is_(True))) or 0,
    }


def _home_context(request: Request, db: Session, **extra) -> dict:
    brands = db.scalars(select(Brand).where(Brand.is_active.is_(True)).order_by(Brand.name)).all()
    installed = set(OllamaClient().installed_models())
    context = {
        "request": request,
        "stats": _stats(db),
        "brands": brands,
        "installed_models": installed,
        "configured_models": list(settings.ollama_models),
        "result": None,
        "form": {"min_budget": 0, "budget": 5000, "usage": "综合体验", "brand": "", "storage": 256, "details": ""},
    }
    context.update(extra)
    return context


@router.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "index.html", _home_context(request, db))


@router.post("/recommend", response_class=HTMLResponse)
def recommendation_page(
    request: Request,
    min_budget: float = Form(0, ge=0, le=30000),
    budget: float = Form(..., ge=500, le=30000),
    usage: str = Form("综合体验"),
    brand: str = Form(""),
    storage: int = Form(256),
    details: str = Form("", max_length=500),
    db: Session = Depends(get_db),
):
    if min_budget > budget:
        raise HTTPException(422, "最低预算不能高于最高预算")
    requirements = Requirements(
        budget=budget,
        usage=usage,
        brand=brand or None,
        min_storage_gb=storage,
        details=details.strip(),
        min_budget=min_budget,
    )
    result = recommend(db, requirements)
    if result.get("results"):
        _attach_like_counts(db, result["results"])
    form = {"min_budget": min_budget, "budget": budget, "usage": usage, "brand": brand, "storage": storage, "details": details}
    return templates.TemplateResponse(request, "index.html", _home_context(request, db, result=result, form=form))


def _phone_library_item(phone: PhoneModel) -> dict:
    verified_prices: list[float] = []
    manual_prices: list[float] = []
    launch_prices: list[float] = []
    manual_statuses = {"manual", "manual_unavailable", "manual_not_found"}
    for variant in phone.variants:
        if not variant.is_active:
            continue
        if variant.launch_price is not None:
            launch_prices.append(float(variant.launch_price))
        for listing in variant.listings:
            if not listing.is_active or not listing.prices:
                continue
            current = latest_snapshot(listing, require_in_stock=True, require_public_price=True)
            value = (current.public_sale_price or current.regular_price) if current else None
            if value is None:
                continue
            if listing.store_verified and current.crawl_status == "reviewed":
                verified_prices.append(float(value))
            elif current.crawl_status in manual_statuses:
                manual_prices.append(float(value))
    prices = verified_prices or manual_prices
    price_kind = "已审核最低" if verified_prices else "手工参考" if manual_prices else None
    important = [phone.cpu, phone.screen_size, phone.refresh_rate, phone.main_camera_mp, phone.battery_mah, phone.weight_g]
    return {
        "record": phone,
        "variant_count": sum(variant.is_active for variant in phone.variants),
        "lowest_price": min(prices) if prices else min(launch_prices) if launch_prices else None,
        "price_kind": price_kind or ("发售价" if launch_prices else None),
        "data_completeness": round(sum(value is not None for value in important) / len(important) * 100),
    }


@router.get("/phones", response_class=HTMLResponse)
def phone_library(
    request: Request,
    brand: str | None = None,
    q: str = "",
    sort: str = "newest",
    db: Session = Depends(get_db),
):
    selected_brand: int | None = None
    if brand and brand.strip():
        try:
            selected_brand = int(brand)
        except ValueError:
            selected_brand = None
    statement = (
        select(PhoneModel)
        .options(
            selectinload(PhoneModel.brand),
            selectinload(PhoneModel.variants)
            .selectinload(PhoneVariant.listings)
            .selectinload(PlatformListing.prices),
        )
        .where(PhoneModel.is_active.is_(True))
    )
    if selected_brand:
        statement = statement.where(PhoneModel.brand_id == selected_brand)
    query_text = q.strip()[:80]
    if query_text:
        statement = statement.join(PhoneModel.brand).where(
            (PhoneModel.model_name.contains(query_text)) | (Brand.name.contains(query_text))
        )
    if sort not in {"newest", "name", "price"}:
        sort = "newest"
    if sort == "name":
        statement = statement.order_by(PhoneModel.model_name)
    else:
        statement = statement.order_by(PhoneModel.release_date.desc().nullslast(), PhoneModel.model_name)
    phones = db.scalars(statement).unique().all()
    brands = db.scalars(select(Brand).where(Brand.is_active.is_(True)).order_by(Brand.name)).all()
    counts = dict(db.execute(
        select(PhoneModel.brand_id, func.count(PhoneModel.id))
        .where(PhoneModel.is_active.is_(True))
        .group_by(PhoneModel.brand_id)
    ).all())
    items = [_phone_library_item(phone) for phone in phones]
    phone_ids = [item["record"].id for item in items]
    likes = _like_counts(db, phone_ids)
    dislikes = _dislike_counts(db, phone_ids)
    for item in items:
        item["like_count"] = likes.get(item["record"].id, 0)
        item["dislike_count"] = dislikes.get(item["record"].id, 0)
    if sort == "price":
        items.sort(key=lambda item: (item["lowest_price"] is None, item["lowest_price"] or 0))
    groups = []
    for current_brand in brands:
        group_items = [item for item in items if item["record"].brand_id == current_brand.id]
        if group_items:
            groups.append({"brand": current_brand, "items": group_items})
    variant_total = sum(item["variant_count"] for item in items)
    verified_total = sum(item["record"].data_quality == "official_verified" for item in items)
    return templates.TemplateResponse(
        request,
        "phones.html",
        {
            "request": request,
            "groups": groups,
            "brands": brands,
            "brand_counts": counts,
            "selected_brand": selected_brand,
            "q": query_text,
            "sort": sort,
            "library_stats": {
                "phones": len(items),
                "variants": variant_total,
                "verified": verified_total,
            },
        },
    )


@router.get("/phones/{phone_id}", response_class=HTMLResponse)
def phone_detail(request: Request, phone_id: int, db: Session = Depends(get_db)):
    phone = db.scalar(
        select(PhoneModel)
        .options(
            selectinload(PhoneModel.brand),
            selectinload(PhoneModel.variants)
            .selectinload(PhoneVariant.listings)
            .selectinload(PlatformListing.prices),
        )
        .where(PhoneModel.id == phone_id, PhoneModel.is_active.is_(True))
    )
    if phone is None:
        return templates.TemplateResponse(request, "404.html", {"request": request}, status_code=404)
    variant_rows = []
    for variant in sorted(
        (item for item in phone.variants if item.is_active),
        key=lambda item: (item.storage_gb, item.ram_gb or 0),
    ):
        listing_by_platform: dict[str, PlatformListing] = {}
        manual_statuses = {"manual", "manual_unavailable", "manual_not_found"}
        for listing in variant.listings:
            if not listing.is_active:
                continue
            if not (
                listing.store_verified
                or any(item_price.crawl_status in manual_statuses for item_price in listing.prices)
            ):
                continue
            listing_by_platform.setdefault(listing.platform, listing)

        search_urls = {
            platform: platform_search_url(
                platform,
                phone.brand.name,
                phone.model_name,
                variant.variant_name,
            )
            for platform in ("jd", "tmall", "pdd")
        }
        quotes = []
        for platform, platform_name in (("jd", "京东"), ("tmall", "天猫"), ("pdd", "拼多多")):
            search_url = search_urls[platform]
            listing = listing_by_platform.get(platform)
            if listing is None:
                quotes.append({
                    "listing": None,
                    "platform": platform,
                    "platform_name": platform_name,
                    "store_name": "尚未建立平台商品",
                    "product_url": None,
                    "search_url": search_url,
                    "purchase_url": search_url,
                    "link_verified": False,
                    "price": None,
                    "in_stock": False,
                    "crawl_status": None,
                    "is_reviewed": False,
                    "has_evidence": False,
                    "freshness_label": "尚未核验",
                    "is_stale": True,
                    "checked_at": None,
                })
                continue
            summary = snapshot_summary(latest_snapshot(listing, require_public_price=True))
            valid_url = (
                listing.product_url
                if listing.product_url and "example.com" not in listing.product_url
                else None
            )
            quotes.append({
                "listing": listing,
                "platform": platform,
                "platform_name": platform_name,
                "store_name": listing.store_name,
                "product_url": valid_url,
                "search_url": search_url,
                "purchase_url": valid_url or search_url,
                "link_verified": bool(valid_url),
                **summary,
            })
        variant_rows.append({
            "variant": variant,
            "quotes": quotes,
            "search_urls": search_urls,
        })
    return templates.TemplateResponse(
        request,
        "phone_detail.html",
        {
            "request": request,
            "phone": phone,
            "variant_rows": variant_rows,
            "like_count": _like_counts(db, [phone.id]).get(phone.id, 0),
            "dislike_count": _dislike_counts(db, [phone.id]).get(phone.id, 0),
        },
    )


@router.post("/api/phones/{phone_id}/like")
def set_phone_like(
    phone_id: int,
    payload: PhoneLikePayload,
    db: Session = Depends(get_db),
) -> dict[str, int | bool]:
    phone = db.scalar(
        select(PhoneModel).where(
            PhoneModel.id == phone_id,
            PhoneModel.is_active.is_(True),
        )
    )
    if phone is None:
        raise HTTPException(status_code=404, detail="手机不存在")
    existing = db.scalar(
        select(PhoneLike).where(
            PhoneLike.phone_id == phone_id,
            PhoneLike.visitor_id == payload.visitor_id,
        )
    )
    if payload.liked and existing is None:
        existing_dislike = db.scalar(
            select(PhoneDislike).where(
                PhoneDislike.phone_id == phone_id,
                PhoneDislike.visitor_id == payload.visitor_id,
            )
        )
        if existing_dislike is not None:
            db.delete(existing_dislike)
        db.add(PhoneLike(phone_id=phone_id, visitor_id=payload.visitor_id))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
    elif not payload.liked and existing is not None:
        db.delete(existing)
        db.commit()
    like_count = db.scalar(
        select(func.count(PhoneLike.id)).where(PhoneLike.phone_id == phone_id)
    ) or 0
    dislike_count = db.scalar(
        select(func.count(PhoneDislike.id)).where(PhoneDislike.phone_id == phone_id)
    ) or 0
    return {
        "phone_id": phone_id,
        "liked": payload.liked,
        "disliked": False,
        "like_count": int(like_count),
        "dislike_count": int(dislike_count),
    }


@router.post("/api/phones/{phone_id}/dislike")
def set_phone_dislike(
    phone_id: int,
    payload: PhoneDislikePayload,
    db: Session = Depends(get_db),
) -> dict[str, int | bool]:
    phone = db.scalar(
        select(PhoneModel).where(
            PhoneModel.id == phone_id,
            PhoneModel.is_active.is_(True),
        )
    )
    if phone is None:
        raise HTTPException(status_code=404, detail="手机不存在")
    existing = db.scalar(
        select(PhoneDislike).where(
            PhoneDislike.phone_id == phone_id,
            PhoneDislike.visitor_id == payload.visitor_id,
        )
    )
    if payload.disliked and existing is None:
        existing_like = db.scalar(
            select(PhoneLike).where(
                PhoneLike.phone_id == phone_id,
                PhoneLike.visitor_id == payload.visitor_id,
            )
        )
        if existing_like is not None:
            db.delete(existing_like)
        db.add(PhoneDislike(phone_id=phone_id, visitor_id=payload.visitor_id))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
    elif not payload.disliked and existing is not None:
        db.delete(existing)
        db.commit()
    like_count = db.scalar(
        select(func.count(PhoneLike.id)).where(PhoneLike.phone_id == phone_id)
    ) or 0
    dislike_count = db.scalar(
        select(func.count(PhoneDislike.id)).where(PhoneDislike.phone_id == phone_id)
    ) or 0
    return {
        "phone_id": phone_id,
        "liked": False,
        "disliked": payload.disliked,
        "like_count": int(like_count),
        "dislike_count": int(dislike_count),
    }


@router.get("/evaluation", response_class=HTMLResponse)
def evaluation_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request,
        "evaluation.html",
        {"request": request, "metrics": build_evaluation_metrics(db)},
    )

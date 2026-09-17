from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.config import BASE_DIR, settings
from app.database import get_db
from app.models import Brand, PhoneModel, PhoneVariant, PlatformListing, PriceSnapshot
from app.services.ollama import OllamaClient
from app.services.evaluation import build_evaluation_metrics
from app.services.recommendation import Requirements, recommend
from app.services.pricing import latest_snapshot, platform_search_url, snapshot_summary


router = APIRouter()
templates = Jinja2Templates(directory=BASE_DIR / "app" / "templates")


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
        {"request": request, "phone": phone, "variant_rows": variant_rows},
    )


@router.get("/evaluation", response_class=HTMLResponse)
def evaluation_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request,
        "evaluation.html",
        {"request": request, "metrics": build_evaluation_metrics(db)},
    )

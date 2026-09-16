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
from app.services.pricing import latest_snapshot, snapshot_summary


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
        "form": {"budget": 5000, "usage": "综合体验", "brand": "", "storage": 256, "details": ""},
    }
    context.update(extra)
    return context


@router.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "index.html", _home_context(request, db))


@router.post("/recommend", response_class=HTMLResponse)
def recommendation_page(
    request: Request,
    budget: float = Form(..., ge=500, le=30000),
    usage: str = Form("综合体验"),
    brand: str = Form(""),
    storage: int = Form(256),
    details: str = Form("", max_length=500),
    db: Session = Depends(get_db),
):
    requirements = Requirements(
        budget=budget,
        usage=usage,
        brand=brand or None,
        min_storage_gb=storage,
        details=details.strip(),
    )
    result = recommend(db, requirements)
    form = {"budget": budget, "usage": usage, "brand": brand, "storage": storage, "details": details}
    return templates.TemplateResponse(request, "index.html", _home_context(request, db, result=result, form=form))


def _phone_library_item(phone: PhoneModel) -> dict:
    prices: list[float] = []
    launch_prices: list[float] = []
    for variant in phone.variants:
        if not variant.is_active:
            continue
        if variant.launch_price is not None:
            launch_prices.append(float(variant.launch_price))
        for listing in variant.listings:
            if not listing.is_active or not listing.store_verified or not listing.prices:
                continue
            current = latest_snapshot(listing, require_in_stock=True, require_public_price=True)
            value = (current.public_sale_price or current.regular_price) if current else None
            if value is not None:
                prices.append(float(value))
    important = [phone.cpu, phone.screen_size, phone.refresh_rate, phone.main_camera_mp, phone.battery_mah, phone.weight_g]
    return {
        "record": phone,
        "variant_count": sum(variant.is_active for variant in phone.variants),
        "lowest_price": min(prices) if prices else min(launch_prices) if launch_prices else None,
        "price_kind": "平台最低" if prices else "发售价" if launch_prices else None,
        "data_completeness": round(sum(value is not None for value in important) / len(important) * 100),
    }


@router.get("/phones", response_class=HTMLResponse)
def phone_library(
    request: Request,
    brand: int | None = None,
    q: str = "",
    sort: str = "newest",
    db: Session = Depends(get_db),
):
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
    if brand:
        statement = statement.where(PhoneModel.brand_id == brand)
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
            "selected_brand": brand,
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
        quotes = []
        for listing in sorted(
            (item for item in variant.listings if item.is_active and item.store_verified),
            key=lambda item: item.platform,
        ):
            summary = snapshot_summary(latest_snapshot(listing, require_public_price=True))
            valid_url = listing.product_url if "example.com" not in listing.product_url else None
            quotes.append({
                "listing": listing,
                "platform_name": {"jd": "京东", "tmall": "天猫", "pdd": "拼多多"}.get(listing.platform, listing.platform),
                "product_url": valid_url,
                **summary,
            })
        variant_rows.append({"variant": variant, "quotes": quotes})
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

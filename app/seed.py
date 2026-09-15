from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Brand, PhoneModel, PhoneVariant, PlatformListing, PriceSnapshot, User
from app.security import hash_password


def ensure_admin(db: Session) -> None:
    existing = db.scalar(select(User).where(User.username == settings.admin_username))
    if existing:
        return
    db.add(
        User(
            username=settings.admin_username,
            password_hash=hash_password(settings.admin_password),
            role="admin",
            is_active=True,
        )
    )
    db.commit()


def seed_demo_data(db: Session) -> None:
    if db.scalar(select(Brand.id).limit(1)):
        return

    now = datetime.now(timezone.utc)
    brands = {
        "华为": Brand(name="华为", official_url="https://consumer.huawei.com/cn/phones/"),
        "小米": Brand(name="小米", sub_brand="REDMI", official_url="https://www.mi.com/"),
        "Apple": Brand(name="Apple", official_url="https://www.apple.com.cn/iphone/"),
        "vivo": Brand(name="vivo", sub_brand="iQOO", official_url="https://www.vivo.com.cn/"),
    }
    db.add_all(brands.values())
    db.flush()

    phones = [
        PhoneModel(
            brand=brands["华为"], model_name="HUAWEI Mate 70", release_date=date(2024, 11, 26),
            sale_status="on_sale", cpu="麒麟系列处理器", screen_size=6.7, screen_type="OLED LTPO",
            resolution="2688 × 1216", refresh_rate=120, main_camera_mp=50,
            camera_summary="5000万像素主摄，支持OIS与潜望式长焦", battery_mah=5300,
            weight_g=203, thickness_mm=7.8, operating_system="HarmonyOS",
            image_url="https://consumer.huawei.com/content/dam/huawei-cbg-site/cn/mkt/pdp/phones/mate70/list/green.png",
            image_source_url="https://consumer.huawei.com/cn/phones/mate70/",
            official_url="https://consumer.huawei.com/cn/phones/mate70/",
            source_url="https://consumer.huawei.com/cn/phones/mate70/specs/",
            source_checked_at=now, data_quality="demo",
        ),
        PhoneModel(
            brand=brands["小米"], model_name="Xiaomi 17", release_date=date(2025, 9, 25),
            sale_status="on_sale", cpu="骁龙旗舰处理器", screen_size=6.3, screen_type="OLED",
            resolution="FHD+", refresh_rate=120, main_camera_mp=50,
            camera_summary="5000万像素主摄，徕卡影像系统", battery_mah=7000,
            charging_w=100, wireless_charging_w=50, weight_g=191,
            image_url="https://i02.appmifile.com/mi-com-product/fly-birds/xiaomi-17/pc/aecbf8da4a890e8f5f2c511f2a04002b.png",
            image_source_url="https://www.mi.com/global/product/xiaomi-17/",
            operating_system="Xiaomi HyperOS", official_url="https://www.mi.com/",
            source_url="https://www.mi.com/", source_checked_at=now, data_quality="demo",
        ),
        PhoneModel(
            brand=brands["Apple"], model_name="iPhone 17", release_date=date(2025, 9, 10),
            sale_status="on_sale", cpu="Apple A19", screen_size=6.3, screen_type="OLED",
            resolution="Super Retina XDR", refresh_rate=120, main_camera_mp=48,
            camera_summary="4800万像素融合式主摄，支持高质量视频拍摄", weight_g=177,
            image_url="https://www.apple.com/v/iphone-17/f/images/meta/iphone-17_overview__cg0rlzmbhl7m_og.png",
            image_source_url="https://www.apple.com.cn/iphone-17/",
            operating_system="iOS", official_url="https://www.apple.com.cn/iphone-17/",
            source_url="https://www.apple.com.cn/iphone/compare/", source_checked_at=now,
            data_quality="demo",
        ),
        PhoneModel(
            brand=brands["vivo"], model_name="vivo X200 Pro", release_date=date(2024, 10, 14),
            sale_status="on_sale", cpu="天玑 9400", screen_size=6.78, screen_type="OLED LTPO",
            resolution="2800 × 1260", refresh_rate=120, main_camera_mp=50,
            camera_summary="蔡司影像，潜望长焦", battery_mah=6000, charging_w=90,
            wireless_charging_w=30, weight_g=223, operating_system="OriginOS",
            image_url="https://wwwstatic.vivo.com.cn/vivoportal/files/resource/product/1767607596306/images/pc/kv.png.webp",
            image_source_url="https://www.vivo.com.cn/vivo/x200pro/",
            official_url="https://www.vivo.com.cn/", source_url="https://www.vivo.com.cn/",
            source_checked_at=now, data_quality="demo",
        ),
    ]
    db.add_all(phones)
    db.flush()

    prices = {
        "HUAWEI Mate 70": (12, 256, 5499, 4899, 4399, 4699, "pdd"),
        "Xiaomi 17": (12, 256, 4499, 4299, 3799, None, "jd"),
        "iPhone 17": (None, 256, 5999, 5299, 4799, None, "tmall"),
        "vivo X200 Pro": (16, 512, 5999, 4999, 4499, None, "jd"),
    }
    platform_names = {"jd": "京东官方旗舰店", "tmall": "天猫官方旗舰店", "pdd": "拼多多官方旗舰店"}
    for phone in phones:
        ram, storage, launch, sale, gov, subsidy, platform = prices[phone.model_name]
        variant = PhoneVariant(
            model=phone, ram_gb=ram, storage_gb=storage,
            variant_name=f"{ram}GB+{storage}GB" if ram else f"{storage}GB",
            launch_price=Decimal(str(launch)), launch_price_source=phone.source_url,
        )
        db.add(variant)
        db.flush()
        listing = PlatformListing(
            variant=variant, platform=platform, store_name=platform_names[platform],
            store_verified=True, external_id=f"demo-{phone.id}", sku_text=variant.variant_name,
            product_title=f"{phone.model_name} {variant.variant_name}", product_url="https://example.com/demo",
            region=settings.target_region, is_active=True, last_checked_at=now,
        )
        db.add(listing)
        db.flush()
        db.add(
            PriceSnapshot(
                listing=listing, regular_price=Decimal(str(launch)),
                public_sale_price=Decimal(str(sale)),
                displayed_gov_price=Decimal(str(gov)) if gov else None,
                estimated_gov_price=Decimal(str(gov)) if gov else None,
                billion_subsidy_price=Decimal(str(subsidy)) if subsidy else None,
                promotion_labels="演示数据", in_stock=True, crawl_status="demo", crawled_at=now,
            )
        )
    db.commit()


def initialize_database(db: Session) -> None:
    ensure_admin(db)
    seed_demo_data(db)
    demo_images = {
        "HUAWEI Mate 70": ("https://consumer.huawei.com/content/dam/huawei-cbg-site/cn/mkt/pdp/phones/mate70/list/green.png", "https://consumer.huawei.com/cn/phones/mate70/"),
        "Xiaomi 17": ("https://i02.appmifile.com/mi-com-product/fly-birds/xiaomi-17/pc/aecbf8da4a890e8f5f2c511f2a04002b.png", "https://www.mi.com/global/product/xiaomi-17/"),
        "iPhone 17": ("https://www.apple.com/v/iphone-17/f/images/meta/iphone-17_overview__cg0rlzmbhl7m_og.png", "https://www.apple.com.cn/iphone-17/"),
        "vivo X200 Pro": ("https://wwwstatic.vivo.com.cn/vivoportal/files/resource/product/1767607596306/images/pc/kv.png.webp", "https://www.vivo.com.cn/vivo/x200pro/"),
    }
    changed = False
    for model_name, (image_url, source_url) in demo_images.items():
        phone = db.scalar(select(PhoneModel).where(PhoneModel.model_name == model_name))
        if phone and phone.data_quality == "demo" and not phone.image_url:
            phone.image_url, phone.image_source_url = image_url, source_url
            changed = True
    if changed:
        db.commit()

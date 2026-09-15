import csv

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.crawlers import market_import
from app.database import Base
from app.models import Brand, PhoneModel, PhoneVariant, PriceSnapshot


def test_reviewed_price_import_is_evidence_checked_and_idempotent(tmp_path, monkeypatch) -> None:
    project = tmp_path / "project"
    evidence = project / "data" / "raw" / "capture.txt"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("public price: 3999", encoding="utf-8")
    monkeypatch.setattr(market_import, "BASE_DIR", project)
    monkeypatch.setattr(market_import, "DATA_DIR", project / "data")

    engine = create_engine(f"sqlite:///{(tmp_path / 'import.db').as_posix()}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    csv_path = tmp_path / "approved.csv"
    fields = [
        "brand", "model_name", "ram_gb", "storage_gb", "platform", "product_url",
        "review_status", "store_name", "reviewer", "evidence_text_path", "external_id",
        "sku_text", "public_sale_price", "promotion_labels", "promotion_stackable", "in_stock",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerow({
            "brand": "测试品牌", "model_name": "测试机", "ram_gb": "12", "storage_gb": "256",
            "platform": "jd", "product_url": "https://item.jd.com/10001.html", "review_status": "approved",
            "store_name": "测试京东自营旗舰店", "reviewer": "reviewer", "evidence_text_path": "data/raw/capture.txt",
            "external_id": "10001", "sku_text": "12GB+256GB", "public_sale_price": "3999",
            "promotion_labels": "限时直降", "promotion_stackable": "unknown", "in_stock": "true",
        })

    with Session() as db:
        brand = Brand(name="测试品牌", is_active=True)
        db.add(brand); db.flush()
        phone = PhoneModel(brand_id=brand.id, model_name="测试机", sale_status="on_sale", is_active=True)
        db.add(phone); db.flush()
        db.add(PhoneVariant(model_id=phone.id, ram_gb=12, storage_gb=256, variant_name="12GB+256GB", is_active=True))
        db.commit()

        first = market_import.import_reviewed_prices(db, csv_path)
        second = market_import.import_reviewed_prices(db, csv_path)
        assert first["snapshots_added"] == 1
        assert second["snapshots_added"] == 0
        assert second["duplicates_skipped"] == 1
        assert db.scalar(select(func.count()).select_from(PriceSnapshot)) == 1

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import select

from app.database import SessionLocal, checkpoint_database
from app.models import PhoneModel, PlatformListing


def main() -> None:
    with SessionLocal() as db:
        listings = db.scalars(select(PlatformListing).where(
            (PlatformListing.external_id.like("demo-%")) | (PlatformListing.product_url.contains("example.com"))
        )).all()
        for listing in listings:
            listing.is_active = False
            listing.store_verified = False
        phones = db.scalars(select(PhoneModel).where(PhoneModel.data_quality == "demo")).all()
        for phone in phones:
            phone.data_quality = "legacy_demo"
        db.commit()
    checkpoint_database()
    print({"retired_listings": len(listings), "legacy_demo_phones": len(phones)})


if __name__ == "__main__":
    main()

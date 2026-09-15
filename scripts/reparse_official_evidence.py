from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import DATA_DIR
from app.crawlers.catalog_pipeline import CatalogSource, CollectedPhone, import_collected, read_sources
from app.crawlers.official_specs import (
    completeness,
    extract_official_image,
    parse_memory_variants,
    parse_official_specs,
    parse_release_date,
)
from app.database import SessionLocal, create_schema, engine


def latest_catalog_report() -> Path:
    reports = sorted((DATA_DIR / "reports").glob("catalog-*.json"), key=lambda path: path.stat().st_mtime)
    if not reports:
        raise FileNotFoundError("No catalog report found under data/reports")
    return reports[-1]


def rebuild_items(report_path: Path, sources_path: Path) -> tuple[list[CollectedPhone], list[dict[str, str]]]:
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    source_map = {(item.brand.casefold(), item.model_name.casefold()): item for item in read_sources(sources_path)}
    collected: list[CollectedPhone] = []
    failures: list[dict[str, str]] = []
    for row in payload.get("items", []):
        key = (row["brand"].casefold(), row["model_name"].casefold())
        source = source_map.get(key) or CatalogSource(row["brand"], row["model_name"], row["url"])
        text_path = Path(row["evidence_path"])
        if not text_path.exists():
            failures.append({"phone": f"{row['brand']} {row['model_name']}", "error": "evidence text missing"})
            continue
        try:
            visible_text = text_path.read_text(encoding="utf-8")
            html_path = text_path.with_suffix(".html")
            html = html_path.read_text(encoding="utf-8") if html_path.exists() else ""
            specs = parse_official_specs(visible_text)
            variants = parse_memory_variants(visible_text)
            if not variants and source.fallback_variants:
                variants = [
                    {
                        "ram_gb": None,
                        "storage_gb": int(value),
                        "variant_name": f"{int(value) if int(value) < 1024 else str(int(value) // 1024) + 'TB'}",
                        "launch_price": None,
                    }
                    for value in source.fallback_variants.split(";") if value.strip()
                ]
            collected.append(CollectedPhone(
                source=source,
                final_url=row["url"],
                fetched_at=datetime.fromtimestamp(text_path.stat().st_mtime, tz=timezone.utc),
                evidence_path=str(text_path.resolve()),
                specs=specs,
                release_date=parse_release_date(visible_text),
                variants=variants,
                image=extract_official_image(html, row["url"], row["model_name"]) if html else None,
                completeness=completeness(specs),
            ))
        except Exception as exc:
            failures.append({"phone": f"{row['brand']} {row['model_name']}", "error": str(exc)})
    return collected, failures


def main() -> None:
    parser = argparse.ArgumentParser(description="Reparse saved official-page evidence without making network requests")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--sources", type=Path, default=PROJECT_ROOT / "config" / "official_catalog_sources.csv")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    report_path = args.report or latest_catalog_report()
    collected, failures = rebuild_items(report_path, args.sources)
    before_with_variants = sum(bool(item.variants) for item in collected)
    counters = {"brands_added": 0, "phones_added": 0, "phones_updated": 0, "variants_added": 0}
    backup_path: Path | None = None
    if not args.dry_run:
        create_schema()
        database_path = Path(engine.url.database or "")
        if database_path.exists():
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup_path = DATA_DIR / "backups" / f"before-evidence-reparse-{stamp}.db"
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(database_path, backup_path)
        with SessionLocal() as db:
            counters = import_collected(db, collected)
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_report": str(report_path.resolve()),
        "network_requests": 0,
        "phones_reparsed": len(collected),
        "phones_with_variants": before_with_variants,
        **counters,
        "failures": failures,
        "backup": str(backup_path.resolve()) if backup_path else None,
    }
    output = DATA_DIR / "reports" / "official-evidence-reparse-latest.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"Report: {output.resolve()}")


if __name__ == "__main__":
    main()

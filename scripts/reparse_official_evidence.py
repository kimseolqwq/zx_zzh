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
from app.crawlers.catalog_pipeline import CatalogSource, CollectedPhone, _fallback_variants, import_collected, read_sources
from app.crawlers.official_specs import (
    completeness,
    extract_official_metadata,
    extract_official_image,
    parse_memory_variants,
    parse_official_specs,
    parse_release_date,
)
from app.database import SessionLocal, checkpoint_database, create_schema, engine


def catalog_reports() -> list[Path]:
    reports = sorted((DATA_DIR / "reports").glob("catalog-*.json"), key=lambda path: path.stat().st_mtime)
    if not reports:
        raise FileNotFoundError("No catalog report found under data/reports")
    return reports


def report_path(path: Path | None) -> str | None:
    if path is None:
        return None
    resolved = path.resolve()
    try:
        return resolved.relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        return resolved.name


def rebuild_items(report_paths: list[Path], sources_paths: list[Path]) -> tuple[list[CollectedPhone], list[dict[str, str]]]:
    sources = [source for path in sources_paths if path.exists() for source in read_sources(path)]
    source_map = {(item.brand.casefold(), item.model_name.casefold()): item for item in sources}
    # Reports may be full or targeted runs.  Preserve the newest successful
    # evidence for every phone instead of assuming the final report is complete.
    latest_rows: dict[tuple[str, str], dict] = {}
    for report_path in sorted(report_paths, key=lambda path: path.stat().st_mtime):
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        for row in payload.get("items", []):
            latest_rows[(row["brand"].casefold(), row["model_name"].casefold())] = row
    collected: list[CollectedPhone] = []
    failures: list[dict[str, str]] = []
    for key, row in latest_rows.items():
        source = source_map.get(key) or CatalogSource(row["brand"], row["model_name"], row["url"])
        text_path = Path(row["evidence_path"])
        if not text_path.is_absolute():
            text_path = PROJECT_ROOT / text_path
        if not text_path.exists():
            failures.append({"phone": f"{row['brand']} {row['model_name']}", "error": "evidence text missing"})
            continue
        try:
            visible_text = text_path.read_text(encoding="utf-8")
            html_path = text_path.with_suffix(".html")
            html = html_path.read_text(encoding="utf-8") if html_path.exists() else ""
            evidence_text = "\n".join(
                part for part in (visible_text, extract_official_metadata(html)) if part
            )
            specs = parse_official_specs(evidence_text)
            variants = parse_memory_variants(evidence_text)
            if not variants and source.fallback_variants:
                variants = _fallback_variants(source.fallback_variants)
            collected.append(CollectedPhone(
                source=source,
                final_url=row["url"],
                fetched_at=datetime.fromtimestamp(text_path.stat().st_mtime, tz=timezone.utc),
                evidence_path=str(text_path.resolve()),
                specs=specs,
                release_date=parse_release_date(evidence_text),
                variants=variants,
                image=extract_official_image(html, row["url"], row["model_name"]) if html else None,
                completeness=completeness(specs),
            ))
        except Exception as exc:
            failures.append({"phone": f"{row['brand']} {row['model_name']}", "error": str(exc)})
    return collected, failures


def main() -> None:
    parser = argparse.ArgumentParser(description="Reparse saved official-page evidence without making network requests")
    parser.add_argument("--report", type=Path, action="append", help="可重复指定；默认合并全部历史采集报告")
    parser.add_argument("--sources", type=Path, action="append", help="可重复指定；默认合并正式与扩展来源清单")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    report_paths = args.report or catalog_reports()
    sources_paths = args.sources or [
        PROJECT_ROOT / "config" / "official_catalog_sources.csv",
        PROJECT_ROOT / "config" / "official_catalog_expansion.csv",
    ]
    collected, failures = rebuild_items(report_paths, sources_paths)
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
        checkpoint_database()
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_reports": [report_path(path) for path in report_paths],
        "network_requests": 0,
        "phones_reparsed": len(collected),
        "phones_with_variants": before_with_variants,
        **counters,
        "failures": failures,
        "backup": report_path(backup_path),
    }
    output = DATA_DIR / "reports" / "official-evidence-reparse-latest.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"Report: {output.resolve()}")


if __name__ == "__main__":
    main()

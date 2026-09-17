from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import zipfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree as ET

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import DATA_DIR
from app.database import SessionLocal, checkpoint_database
from app.models import PhoneModel, PhoneVariant, PlatformListing, PriceSnapshot


MAIN_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL_NS = {
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "p": "http://schemas.openxmlformats.org/package/2006/relationships",
}


def column_index(reference: str) -> int:
    letters = re.match(r"[A-Z]+", reference.upper())
    if not letters:
        return 0
    value = 0
    for char in letters.group(0):
        value = value * 26 + ord(char) - 64
    return value - 1


def read_xlsx(path: Path) -> list[list[str]]:
    with zipfile.ZipFile(path) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root.findall("m:si", MAIN_NS):
                shared.append("".join(node.text or "" for node in item.iterfind(".//m:t", MAIN_NS)))
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        first_sheet = workbook.find("m:sheets/m:sheet", MAIN_NS)
        if first_sheet is None:
            raise ValueError("Excel 中没有工作表")
        relation_id = first_sheet.attrib[f"{{{REL_NS['r']}}}id"]
        relations = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        target = next(
            item.attrib["Target"]
            for item in relations.findall("p:Relationship", REL_NS)
            if item.attrib.get("Id") == relation_id
        )
        sheet_path = "xl/" + target.lstrip("/")
        root = ET.fromstring(archive.read(sheet_path))
        rows: list[list[str]] = []
        for row in root.findall(".//m:sheetData/m:row", MAIN_NS):
            values: list[str] = []
            for cell in row.findall("m:c", MAIN_NS):
                index = column_index(cell.attrib.get("r", "A1"))
                while len(values) <= index:
                    values.append("")
                cell_type = cell.attrib.get("t")
                if cell_type == "inlineStr":
                    value = "".join(node.text or "" for node in cell.iterfind(".//m:t", MAIN_NS))
                else:
                    node = cell.find("m:v", MAIN_NS)
                    value = node.text if node is not None and node.text is not None else ""
                    if cell_type == "s" and value:
                        value = shared[int(value)]
                values[index] = value.strip()
            rows.append(values)
    return rows


def canonical_variant(value: str) -> tuple[int | None, int] | None:
    compact = re.sub(r"\s+", "", str(value).upper()).replace("＋", "+")
    pair = re.fullmatch(r"(?:(\d{1,2})GB\+)?(\d{1,4})(GB|TB)?", compact)
    if not pair:
        return None
    ram = int(pair.group(1)) if pair.group(1) else None
    storage = int(pair.group(2))
    if (pair.group(3) or "").upper() == "TB":
        storage *= 1024
    return ram, storage


def money(value: str, *, launch: bool = False) -> Decimal | None:
    text = str(value or "").strip().replace(",", "")
    if not text or text in {"无货", "未查询到", "无数据", "未采集"}:
        return None
    if not re.fullmatch(r"\d+(?:\.\d+)?", text):
        raise ValueError(f"无效价格：{text}")
    result = Decimal(text).quantize(Decimal("0.01"))
    if not Decimal("300") <= result <= Decimal("30000"):
        raise ValueError(f"价格超出范围：{text}")
    return result


def status_price(value: str) -> tuple[Decimal | None, bool, str]:
    text = str(value or "").strip()
    if not text:
        return None, True, "manual_not_found"
    if text == "无货":
        return None, False, "manual_unavailable"
    if text == "未查询到":
        return None, True, "manual_not_found"
    return money(text), True, "manual"


def portable(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def import_workbook(path: Path, *, apply: bool) -> dict:
    rows = read_xlsx(path)
    if not rows or [value.strip() for value in rows[0][:5]] != ["序号", "品牌", "机型", "内存/存储", "发售价"]:
        raise ValueError("Excel 表头不符合价格采集模板")
    data_rows = [row for row in rows[1:] if row and row[0].strip()]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    evidence_dir = DATA_DIR / "raw" / "ecommerce" / "manual"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence_path = evidence_dir / f"人工采集价格结果-{hashlib.sha256(path.read_bytes()).hexdigest()[:8]}.xlsx"
    if apply and not evidence_path.exists():
        shutil.copy2(path, evidence_path)
    evidence_rel = portable(evidence_path)
    now = datetime.now(timezone.utc)
    report: dict = {
        "source": str(path),
        "evidence": evidence_rel,
        "rows": len(data_rows),
        "matched": 0,
        "mismatches": [],
        "launch_prices_filled": 0,
        "snapshots_added": 0,
        "duplicates": 0,
        "platform_counts": {"jd": 0, "tmall": 0, "pdd": 0},
        "status_counts": {"manual": 0, "manual_unavailable": 0, "manual_not_found": 0},
    }
    with SessionLocal() as db:
        variants = db.scalars(
            select(PhoneVariant)
            .options(selectinload(PhoneVariant.model).selectinload(PhoneModel.brand), selectinload(PhoneVariant.listings))
            .where(PhoneVariant.is_active.is_(True))
        ).unique().all()
        by_name: dict[tuple[str, str, tuple[int | None, int]], PhoneVariant] = {}
        for variant in variants:
            key = (variant.model.brand.name, variant.model.model_name, (variant.ram_gb, variant.storage_gb))
            by_name[key] = variant
        for row in data_rows:
            row_no = int(float(row[0]))
            brand, model, raw_variant = row[1], row[2], row[3]
            canonical = canonical_variant(raw_variant)
            variant = by_name.get((brand, model, canonical)) if canonical else None
            if variant is None:
                report["mismatches"].append({"row": row_no, "brand": brand, "model": model, "variant": raw_variant})
                continue
            report["matched"] += 1
            launch = money(row[4], launch=True) if len(row) > 4 else None
            if launch is not None and variant.launch_price is None:
                variant.launch_price = launch
                variant.launch_price_source = evidence_rel
                report["launch_prices_filled"] += 1
            for offset, platform in enumerate(("jd", "tmall", "pdd"), start=5):
                raw_price = row[offset] if len(row) > offset else ""
                price, in_stock, crawl_status = status_price(raw_price)
                if price is None and crawl_status == "manual_not_found" and not str(raw_price).strip():
                    continue
                external_id = f"manual-excel-{variant.id}-{platform}"
                sku_text = variant.variant_name
                listing = next(
                    (
                        item for item in variant.listings
                        if item.platform == platform and item.external_id == external_id and item.sku_text == sku_text
                    ),
                    None,
                )
                if listing is None:
                    listing = PlatformListing(
                        variant_id=variant.id,
                        platform=platform,
                        store_name="用户手工采集Excel（店铺未核验）",
                        store_verified=False,
                        external_id=external_id,
                        sku_text=sku_text,
                        product_title=f"{brand} {model} {variant.variant_name}",
                        product_url="",
                        region="中国大陆",
                        is_active=True,
                        last_checked_at=now,
                    )
                    db.add(listing)
                    db.flush()
                    variant.listings.append(listing)
                else:
                    listing.store_name = "用户手工采集Excel（店铺未核验）"
                    listing.store_verified = False
                    listing.last_checked_at = now
                    listing.is_active = True
                previous = db.scalar(
                    select(PriceSnapshot)
                    .where(PriceSnapshot.listing_id == listing.id)
                    .order_by(PriceSnapshot.crawled_at.desc(), PriceSnapshot.id.desc())
                    .limit(1)
                )
                label = "用户手工采集Excel（店铺/SKU未提供）" if price is not None else f"用户手工采集Excel：{'无货' if not in_stock else '未查询到'}"
                duplicate = previous is not None and all((
                    previous.public_sale_price == price,
                    previous.regular_price is None,
                    previous.in_stock is in_stock,
                    previous.crawl_status == crawl_status,
                    previous.promotion_labels == label,
                    previous.evidence_text_path == evidence_rel,
                ))
                if duplicate:
                    report["duplicates"] += 1
                else:
                    db.add(PriceSnapshot(
                        listing_id=listing.id,
                        regular_price=None,
                        public_sale_price=price,
                        promotion_labels=label,
                        promotion_stackable="unknown",
                        in_stock=in_stock,
                        crawl_status=crawl_status,
                        evidence_text_path=evidence_rel,
                        crawled_at=now,
                    ))
                    report["snapshots_added"] += 1
                report["platform_counts"][platform] += 1
                report["status_counts"][crawl_status] += 1
        if report["mismatches"]:
            db.rollback()
            sample = json.dumps(report["mismatches"][:12], ensure_ascii=False)
            raise ValueError(f"有 {len(report['mismatches'])} 行无法匹配，已整批回滚；示例：{sample}")
        if apply:
            db.commit()
            checkpoint_database()
        else:
            db.rollback()
    report_path = DATA_DIR / "reports" / f"manual-price-import-{stamp}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["report"] = str(report_path.resolve())
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="导入手工价格 Excel；默认 dry-run，使用 --apply 正式入库")
    parser.add_argument("file", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(import_workbook(args.file, apply=args.apply), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

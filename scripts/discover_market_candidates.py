from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import async_playwright

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import DATA_DIR
from app.crawlers.market_candidates import candidate_score, normalize_product_url


FIELDS = (
    "brand", "model_name", "ram_gb", "storage_gb", "variant_name", "platform", "search_url",
    "candidate_rank", "candidate_score", "product_title", "product_url", "context_text",
    "evidence_screenshot", "review_status", "review_note",
)
BLOCKED_WORDS = ("验证码", "滑块", "访问过于频繁", "访问频繁", "无法搜索", "安全验证")


def _portable_path(value: str) -> str:
    if not value:
        return ""
    candidate = Path(value)
    if not candidate.is_absolute():
        return candidate.as_posix()
    try:
        return candidate.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        return value


def _write(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows([
            {**row, "evidence_screenshot": _portable_path(row.get("evidence_screenshot", ""))}
            for row in rows
        ])
    temporary.replace(path)


async def discover(queue: Path, output: Path, *, platform: str, limit: int, headless: bool) -> dict[str, int]:
    with queue.open("r", encoding="utf-8-sig", newline="") as handle:
        source_rows = [row for row in csv.DictReader(handle) if row.get("platform") == platform]
    existing: list[dict[str, str]] = []
    if output.exists():
        with output.open("r", encoding="utf-8-sig", newline="") as handle:
            existing = list(csv.DictReader(handle))
    completed_keys = {
        (row.get("platform", ""), row.get("model_name", ""), row.get("ram_gb", ""), row.get("storage_gb", ""))
        for row in existing
        if row.get("review_status", "").strip().lower() not in {"blocked", "failed", "no_candidates"}
    }
    counters = {"searched": 0, "candidates": 0, "blocked": 0, "failed": 0}
    evidence_dir = DATA_DIR / "raw" / "ecommerce" / "searches"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    profile_dir = DATA_DIR / "browser-profile"
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=profile_dir, channel="msedge", headless=headless,
            locale="zh-CN", viewport={"width": 1440, "height": 1000},
        )
        page = context.pages[0] if context.pages else await context.new_page()
        for row in source_rows:
            key = (platform, row.get("model_name", ""), row.get("ram_gb", ""), row.get("storage_gb", ""))
            if key in completed_keys or not row.get("search_url", "").strip():
                continue
            if counters["searched"] >= limit:
                break
            counters["searched"] += 1
            try:
                await page.goto(row["search_url"], wait_until="domcontentloaded", timeout=60_000)
                await page.wait_for_timeout(4_000)
                body = await page.locator("body").inner_text(timeout=15_000)
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                screenshot = evidence_dir / f"{platform}-{counters['searched']:03d}-{stamp}.png"
                await page.screenshot(path=screenshot, full_page=True)
                blocked = next((word for word in BLOCKED_WORDS if word in body), None)
                if not blocked:
                    verification = page.locator(
                        '[class*="captcha"], [class*="verify"], [class*="nc-container"], iframe[src*="captcha"]'
                    )
                    if await verification.count() and await verification.first.is_visible():
                        blocked = "页面验证控件"
                if blocked:
                    counters["blocked"] += 1
                    existing.append({
                        **{field: row.get(field, "") for field in FIELDS},
                        "candidate_rank": "0", "candidate_score": "0", "context_text": blocked,
                        "evidence_screenshot": str(screenshot), "review_status": "blocked",
                        "review_note": f"页面要求人工处理：{blocked}",
                    })
                    _write(output, existing)
                    continue
                anchors = await page.locator("a[href]").evaluate_all(
                    """anchors => anchors.map(a => ({
                        href: a.href || a.getAttribute('href') || '',
                        title: (a.innerText || a.getAttribute('title') || '').trim(),
                        context: (a.closest('li, article')?.innerText || a.parentElement?.parentElement?.innerText || a.parentElement?.innerText || '').trim()
                    }))"""
                )
                candidates: dict[str, dict[str, str | int]] = {}
                for anchor in anchors:
                    product_url = normalize_product_url(platform, anchor.get("href", ""), row["search_url"])
                    if not product_url:
                        continue
                    context_text = (anchor.get("context") or anchor.get("title") or "")[:1000]
                    score = candidate_score(row.get("model_name", ""), row.get("variant_name", ""), context_text)
                    candidate = {"url": product_url, "title": (anchor.get("title") or "")[:300], "context": context_text, "score": score}
                    if product_url not in candidates or score > int(candidates[product_url]["score"]):
                        candidates[product_url] = candidate
                ranked = sorted(candidates.values(), key=lambda item: int(item["score"]), reverse=True)[:5]
                if not ranked:
                    existing.append({
                        **{field: row.get(field, "") for field in FIELDS},
                        "candidate_rank": "0", "candidate_score": "0",
                        "evidence_screenshot": str(screenshot), "review_status": "no_candidates",
                        "review_note": "页面已打开，但未发现可识别的商品详情链接；允许后续重试",
                    })
                for rank, candidate in enumerate(ranked, start=1):
                    existing.append({
                        **{field: row.get(field, "") for field in FIELDS},
                        "candidate_rank": str(rank), "candidate_score": str(candidate["score"]),
                        "product_title": str(candidate["title"]), "product_url": str(candidate["url"]),
                        "context_text": str(candidate["context"]), "evidence_screenshot": str(screenshot),
                        "review_status": "pending", "review_note": "需核对店铺与内存版本",
                    })
                counters["candidates"] += len(ranked)
                completed_keys.add(key)
                _write(output, existing)
            except Exception as exc:
                counters["failed"] += 1
                existing.append({
                    **{field: row.get(field, "") for field in FIELDS},
                    "candidate_rank": "0", "candidate_score": "0", "review_status": "failed",
                    "review_note": str(exc)[:500],
                })
                _write(output, existing)
        await context.close()
    return counters


def main() -> None:
    parser = argparse.ArgumentParser(description="从三平台搜索页生成商品详情链接候选审核表")
    parser.add_argument("file", type=Path, help="价格采集队列 CSV")
    parser.add_argument("--platform", required=True, choices=("jd", "tmall", "pdd"))
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--output", type=Path, default=DATA_DIR / "review" / "market_candidates.csv")
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit 必须大于 0")
    print(asyncio.run(discover(args.file, args.output, platform=args.platform, limit=args.limit, headless=args.headless)))


if __name__ == "__main__":
    main()

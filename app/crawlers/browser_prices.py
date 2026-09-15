from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import async_playwright

from app.config import DATA_DIR
from app.crawlers.price_parser import parse_price_text


async def capture_public_price_page(url: str, item_id: str, *, headless: bool = False) -> dict:
    """打开公开商品页并留证；不绕过登录、滑块或验证码。"""
    auth_dir = DATA_DIR / "browser-profile"
    auth_dir.mkdir(parents=True, exist_ok=True)
    # Evidence used by the reviewed importer must stay below data/raw.  Keeping
    # screenshots beside the extracted text also makes one capture auditable as
    # a pair and ensures neither file is committed to Git.
    evidence_dir = DATA_DIR / "raw" / "ecommerce" / "screenshots"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = DATA_DIR / "raw" / "ecommerce"
    raw_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=auth_dir,
            channel="msedge",
            headless=headless,
            locale="zh-CN",
            viewport={"width": 1440, "height": 1000},
        )
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        await page.wait_for_timeout(3500)
        title = await page.title()
        text = await page.locator("body").inner_text(timeout=15_000)
        screenshot = evidence_dir / f"{item_id}-{stamp}.png"
        text_path = raw_dir / f"{item_id}-{stamp}.txt"
        await page.screenshot(path=screenshot, full_page=True)
        text_path.write_text(text, encoding="utf-8")
        parsed = parse_price_text(text)
        blocked_words = ["验证码", "滑块", "登录后查看", "访问过于频繁"]
        blocked = next((word for word in blocked_words if word in text), None)
        await context.close()
    return {
        "url": url,
        "title": title,
        "captured_at": stamp,
        "blocked_reason": blocked,
        "status": "blocked" if blocked else "needs_review" if parsed.confidence != "high" else "success",
        "parsed": asdict(parsed),
        "text_path": str(text_path),
        "screenshot_path": str(screenshot),
    }


def capture(url: str, item_id: str, *, headless: bool = False) -> dict:
    return asyncio.run(capture_public_price_page(url, item_id, headless=headless))

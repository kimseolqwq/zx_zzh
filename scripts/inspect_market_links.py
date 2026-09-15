from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import DATA_DIR


ALLOWED_HOST_SUFFIXES = ("tmall.com", "taobao.com", "jd.com", "yangkeduo.com")


async def inspect(url: str, *, headless: bool) -> dict:
    profile = DATA_DIR / "browser-profile"
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=profile, channel="msedge", headless=headless,
            locale="zh-CN", viewport={"width": 1440, "height": 1000},
        )
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        await page.wait_for_timeout(4_000)
        raw_links = await page.locator("a[href]").evaluate_all(
            """anchors => anchors.map(a => ({
                href: a.href || '',
                text: (a.innerText || a.getAttribute('title') || a.getAttribute('aria-label') || '').trim(),
                context: (a.parentElement?.innerText || '').trim()
            }))"""
        )
        title = await page.title()
        final_url = page.url
        await context.close()
    links: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw_links:
        href = item.get("href", "")
        host = (urlparse(href).hostname or "").lower()
        if not any(host == suffix or host.endswith(f".{suffix}") for suffix in ALLOWED_HOST_SUFFIXES):
            continue
        if href in seen:
            continue
        seen.add(href)
        links.append({
            "href": href, "text": item.get("text", "")[:300],
            "context": item.get("context", "")[:500],
        })
    return {"title": title, "final_url": final_url, "link_count": len(links), "links": links[:200]}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="只读检查商品页暴露的站内店铺和商品链接")
    parser.add_argument("url")
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(inspect(args.url, headless=args.headless)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

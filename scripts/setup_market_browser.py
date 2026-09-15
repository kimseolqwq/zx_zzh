from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import DATA_DIR


PLATFORM_HOME_PAGES = (
    ("京东", "https://www.jd.com/"),
    ("天猫", "https://www.tmall.com/"),
    ("拼多多", "https://mobile.yangkeduo.com/"),
)


async def setup() -> None:
    profile_dir = DATA_DIR / "browser-profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            channel="msedge",
            headless=False,
            locale="zh-CN",
            viewport={"width": 1440, "height": 1000},
        )
        existing = context.pages
        for index, (name, url) in enumerate(PLATFORM_HOME_PAGES):
            page = existing[0] if index == 0 and existing else await context.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            except Exception as exc:
                print(f"{name}页面打开失败，可在窗口中手动重试：{exc}")
        print("请在专用 Edge 窗口中分别登录京东、天猫和拼多多。")
        print("全部登录完成后关闭整个专用 Edge 窗口，登录状态会保存在本机。")
        while context.pages:
            await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(setup())

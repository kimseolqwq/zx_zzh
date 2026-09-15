from __future__ import annotations

import hashlib
import time
import urllib.robotparser
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from app.config import DATA_DIR


USER_AGENT = "PhoneRecommendationCourseProject/1.0 (+local academic project; low-frequency)"


@dataclass
class FetchResult:
    url: str
    status_code: int
    html: str
    visible_text: str
    fetched_at: datetime
    html_path: Path
    text_path: Path
    sha256: str


class SafeFetcher:
    """尊重 robots.txt、限速、保留证据的静态页面下载器。"""

    def __init__(self, minimum_interval: float = 3.0, timeout: float = 25.0) -> None:
        self.minimum_interval = minimum_interval
        self.timeout = timeout
        self._last_request: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}

    def _allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        root = f"{parsed.scheme}://{parsed.netloc}"
        if root not in self._robots:
            parser = urllib.robotparser.RobotFileParser(f"{root}/robots.txt")
            try:
                parser.read()
            except OSError:
                return False
            self._robots[root] = parser
        return self._robots[root].can_fetch(USER_AGENT, url)

    def fetch(self, url: str, *, respect_robots: bool = True) -> FetchResult:
        if respect_robots and not self._allowed(url):
            raise PermissionError(f"robots.txt 不允许采集：{url}")
        host = urlparse(url).netloc
        elapsed = time.monotonic() - self._last_request.get(host, 0)
        if elapsed < self.minimum_interval:
            time.sleep(self.minimum_interval - elapsed)
        with httpx.Client(
            timeout=self.timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9"},
        ) as client:
            response = client.get(url)
            self._last_request[host] = time.monotonic()
            response.raise_for_status()
        html = response.text
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()
        visible_text = "\n".join(line.strip() for line in soup.get_text("\n").splitlines() if line.strip())
        fetched_at = datetime.now(timezone.utc)
        digest = hashlib.sha256(html.encode("utf-8")).hexdigest()
        stamp = fetched_at.strftime("%Y%m%dT%H%M%SZ")
        folder = DATA_DIR / "raw" / host.replace(":", "_")
        folder.mkdir(parents=True, exist_ok=True)
        html_path = folder / f"{stamp}-{digest[:10]}.html"
        text_path = folder / f"{stamp}-{digest[:10]}.txt"
        html_path.write_text(html, encoding="utf-8")
        text_path.write_text(visible_text, encoding="utf-8")
        return FetchResult(
            url=str(response.url), status_code=response.status_code, html=html,
            visible_text=visible_text, fetched_at=fetched_at, html_path=html_path,
            text_path=text_path, sha256=digest,
        )


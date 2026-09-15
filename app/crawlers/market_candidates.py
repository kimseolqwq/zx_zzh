from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse


PRODUCT_URL_PATTERNS = {
    "jd": re.compile(r"^https://item\.jd\.com/\d+\.html", re.IGNORECASE),
    "tmall": re.compile(r"^https://(?:detail\.tmall\.com|item\.taobao\.com)/item\.htm", re.IGNORECASE),
    "pdd": re.compile(r"^https://mobile\.yangkeduo\.com/goods\.html", re.IGNORECASE),
}


def normalize_product_url(platform: str, href: str, base_url: str) -> str | None:
    if platform not in PRODUCT_URL_PATTERNS or not href:
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    url = urljoin(base_url, href)
    if not PRODUCT_URL_PATTERNS[platform].search(url):
        return None
    parsed = urlparse(url)
    if platform == "jd":
        return f"https://item.jd.com{parsed.path}"
    if platform == "pdd":
        match = re.search(r"(?:^|&)goods_id=(\d+)", parsed.query)
        return f"https://mobile.yangkeduo.com/goods.html?goods_id={match.group(1)}" if match else None
    item_match = re.search(r"(?:^|&)id=(\d+)", parsed.query)
    host = "detail.tmall.com" if parsed.hostname == "detail.tmall.com" else "item.taobao.com"
    return f"https://{host}/item.htm?id={item_match.group(1)}" if item_match else None


def candidate_score(model_name: str, variant_name: str, context_text: str) -> int:
    compact_context = re.sub(r"\s+", "", context_text).lower()
    compact_model = re.sub(r"\s+", "", model_name).lower()
    score = 0
    if compact_model and compact_model in compact_context:
        score += 10
    else:
        for token in re.findall(r"[a-z]+|\d+|[\u4e00-\u9fff]+", model_name.lower()):
            if len(token) >= 2 and token in compact_context:
                score += 1
    storage_matches = re.findall(r"\d+\s*(?:gb|g|tb)", variant_name.lower())
    score += sum(3 for value in storage_matches if re.sub(r"\s+", "", value) in compact_context)
    if "官方旗舰店" in compact_context:
        score += 8
    elif "旗舰店" in compact_context:
        score += 4
    if "自营" in compact_context:
        score += 6
    if any(label in compact_context for label in ("二手", "资源机", "展示机", "租赁", "手机壳")):
        score -= 12
    return score

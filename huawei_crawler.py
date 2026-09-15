# -*- coding: utf-8 -*-
"""
华为官网手机全系列规格 + 价格爬虫（完整版）
覆盖：Pura / Mate / Pocket / nova /畅享 五个系列

数据来源：
  - 列表页: https://consumer.huawei.com/cn/phones/  （提取机型列表 + 价格）
  - 规格页: https://consumer.huawei.com/cn/phones/{slug}/specs/  （提取完整规格）

输出：
  - huawei_data/huawei_phones_full.json  （完整结构化数据，含价格、系列、规格）
  - huawei_data/huawei_phones_full.csv   （扁平化，可直接入库）
"""

import json
import re
import time
import csv
import os
import html as html_lib
from datetime import datetime

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9",
}

BASE = "https://consumer.huawei.com/cn/phones"
REQUEST_INTERVAL = 1.2
TIMEOUT = 20
MAX_RETRY = 3

# 列表页没有价格、但规格页存在的机型，手动补充
EXTRA_SLUGS = [
    "pocket-2",                      # Pocket 系列
    "mate-xt-ultimate-design",       # Mate XT 非凡大师（三折叠）
]


def fetch(url: str) -> str:
    for attempt in range(1, MAX_RETRY + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or "utf-8"
            return resp.text
        except Exception as e:
            print(f"   [重试 {attempt}/{MAX_RETRY}] {url} 失败: {e}")
            time.sleep(REQUEST_INTERVAL * 2)
    return ""


def clean_name(name: str) -> str:
    """清理产品名称中的 HTML 标签和特殊实体"""
    name = re.sub(r"<[^>]+>", "", name)
    name = html_lib.unescape(name)
    name = name.replace("\u00a0", " ").replace("&NoBreak;", "")
    return re.sub(r"\s+", " ", name).strip()


def classify_series(slug: str, name: str) -> str:
    if "pocket" in slug:
        return "Pocket 系列"
    if slug.startswith("pura"):
        return "Pura 系列"
    if slug.startswith("mate"):
        return "Mate 系列"
    if slug.startswith("nova"):
        return "nova 系列"
    if slug.startswith("changxiang") or "畅享" in name:
        return "畅享系列"
    return "其他"


def extract_products_from_list() -> dict:
    """
    从列表页内嵌的 JSON 数据中提取所有机型。
    返回 {slug: {"name": ..., "price": ..., "series": ...}}
    """
    raw = fetch(f"{BASE}/")
    if not raw:
        return {}
    decoded = html_lib.unescape(raw)
    products = {}

    # 结构一：shortProductLink + 顶层 price（最新主推 4 款）
    for m in re.finditer(r'"shortProductLink":"(/cn/phones/[^"]+)"', decoded):
        link = m.group(1)
        slug = link.rstrip("/").split("/")[-1]
        seg = decoded[m.start():m.start() + 1500]
        name_m = re.search(r'"productTitle":"([^"]*)"', seg)
        price_m = re.search(r'"price":"([^"]*)"', seg)
        name = clean_name(name_m.group(1)) if name_m else slug
        price = price_m.group(1) if price_m else ""
        if slug not in products:
            products[slug] = {"name": name, "price": price}

    # 结构二：productLink + data.price（全系列 60+ 款）
    for m in re.finditer(r'"productLink":"(/cn/phones/[^"]+)"', decoded):
        link = m.group(1)
        slug = link.rstrip("/").split("/")[-1]
        seg = decoded[m.start():m.start() + 2000]
        name_m = re.search(r'"productTitle":"([^"]*)"', seg)
        # 价格可能在 data.price 或顶层 price
        price_m = re.search(r'"price":"([^"]*)"', seg)
        name = clean_name(name_m.group(1)) if name_m else slug
        price = price_m.group(1) if price_m else ""
        if slug not in products:
            products[slug] = {"name": name, "price": price}
        elif not products[slug]["price"] and price:
            products[slug]["price"] = price

    # 补充手动添加的机型
    for slug in EXTRA_SLUGS:
        if slug not in products:
            products[slug] = {"name": slug.replace("-", " ").title(), "price": ""}

    # 标注系列
    for slug, info in products.items():
        info["series"] = classify_series(slug, info["name"])

    return products


def parse_specs(html_text: str) -> dict:
    """解析规格页，按手风琴分组提取键值对"""
    soup = BeautifulSoup(html_text, "lxml")
    groups = {}
    for acc in soup.select(".large-accordion__button"):
        title_el = acc.find(class_="large-accordion-title")
        if not title_el:
            continue
        group_name = title_el.get_text(strip=True)
        content = None
        node = acc.parent
        for _ in range(4):
            c = node.select_one(".large-accordion__content")
            if c:
                content = c
                break
            node = node.parent
        if content is None:
            continue
        pairs = {}
        for sub in content.select("[class*='large-accordion-subtitle']"):
            key = sub.get_text(strip=True)
            if not key:
                continue
            p = sub.find_next("p")
            value = p.get_text(" ", strip=True) if p else ""
            if key not in pairs:
                pairs[key] = value
        if pairs:
            groups[group_name] = pairs
    return groups


def crawl_one(slug: str, meta: dict) -> dict:
    url = f"{BASE}/{slug}/specs/"
    html_text = fetch(url)
    if not html_text:
        return None
    soup = BeautifulSoup(html_text, "lxml")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    name = re.sub(r"\s*(参数规格|规格参数|规格|参数)\s*[-–—]?\s*华为官网.*$", "", title).strip()
    if not name:
        name = meta.get("name", slug)
    specs = parse_specs(html_text)
    return {
        "name": name,
        "slug": slug,
        "series": meta.get("series", "其他"),
        "price": meta.get("price", ""),
        "url": url,
        "crawl_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "华为消费者业务官网",
        "specs": specs,
    }


def flatten(phone: dict) -> dict:
    flat = {
        "series": phone["series"],
        "name": phone["name"],
        "price": phone["price"],
        "url": phone["url"],
    }
    for group, pairs in phone["specs"].items():
        for k, v in pairs.items():
            flat[f"{group}_{k}"] = v
    return flat


def main():
    print("=" * 55)
    print("  华为手机全系列规格 + 价格爬虫")
    print("=" * 55)

    print("\n[1/3] 从列表页提取机型列表...")
    products = extract_products_from_list()
    # 只保留五个系列
    target = {s: info for s, info in products.items()
              if info["series"] in ("Pura 系列", "Mate 系列", "Pocket 系列", "nova 系列", "畅享系列")}

    from collections import Counter
    series_count = Counter(info["series"] for info in target.values())
    print(f"  共发现 {len(target)} 款机型：")
    for s, c in sorted(series_count.items()):
        print(f"    {s}: {c} 款")

    print(f"\n[2/3] 逐个采集规格页（间隔 {REQUEST_INTERVAL}s）...")
    phones = []
    failed = []
    for i, (slug, meta) in enumerate(sorted(target.items()), 1):
        print(f"  [{i}/{len(target)}] {meta['series'][:4]} | {slug:32s}", end=" ")
        data = crawl_one(slug, meta)
        if data:
            n_spec = sum(len(v) for v in data["specs"].values())
            if n_spec == 0:
                print(f"-> 无规格数据（已跳过）")
                failed.append(slug)
                time.sleep(REQUEST_INTERVAL)
                continue
            price_str = f"￥{data['price']}" if data["price"] else "无价格"
            print(f"-> {data['name'][:20]:20s} | {price_str:8s} | {n_spec} 字段")
            phones.append(data)
        else:
            print("-> 失败")
            failed.append(slug)
        time.sleep(REQUEST_INTERVAL)

    print(f"\n[3/3] 保存数据...")
    os.makedirs("data", exist_ok=True)

    json_path = "huawei_data/huawei_phones_full.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(phones, f, ensure_ascii=False, indent=2)
    print(f"  JSON: {json_path} ({len(phones)} 条)")

    if phones:
        csv_path = "huawei_data/huawei_phones_full.csv"
        flat = [flatten(p) for p in phones]
        keys = []
        for row in flat:
            for k in row:
                if k not in keys:
                    keys.append(k)
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            writer.writeheader()
            writer.writerows(flat)
        print(f"  CSV:  {csv_path}")

    if failed:
        print(f"\n  ⚠ 采集失败 {len(failed)} 款: {failed}")
    print("\n完成！")


if __name__ == "__main__":
    main()

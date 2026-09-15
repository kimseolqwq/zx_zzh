# -*- coding: utf-8 -*-
"""
OPPO 官网手机全系列规格爬虫（完整版）

数据来源：
  - 列表页: https://www.oppo.com/cn/smartphones/ （提取全部机型链接）
  - 规格页: https://www.oppo.com/cn/smartphones/series-{系列}/{型号}/specs/ （服务端渲染，直接 requests 采集）

说明：
  OPPO 规格页是服务端渲染的，不需要 Playwright，用 requests 即可快速采集。
  价格在 OPPO 商城(opposhop.cn)，本爬虫先采集规格，价格可后续补充。

安装依赖：
  pip install requests beautifulsoup4

输出：
  - oppo_data/oppo_phones.json  （完整结构化数据）
  - oppo_data/oppo_phones.csv   （扁平化，可直接入库）
"""

import json
import re
import time
import csv
import os
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

LIST_URL = "https://www.oppo.com/cn/smartphones/"
SPECS_URL_TEMPLATE = "https://www.oppo.com/cn/smartphones/series-{series}/{model}/specs/"
REQUEST_INTERVAL = 0.5
TIMEOUT = 20

# 需要排除的非手机产品链接
EXCLUDE_KEYWORDS = [
    "/3d/", "tablet", "watch", "headphone", "earphone",
    "accessory", "pad", "book", "tv",
]

# 规格分组标题（用于识别分组）
SPEC_GROUPS = [
    "尺寸与重量", "存储", "显示", "摄像头", "视频拍摄", "电池", "充电",
    "处理器", "操作系统", "网络", "连接", "导航定位", "传感器", "多媒体",
    "生物识别", "SIM 卡", "SIM卡", "机身", "防护", "包装", "其他",
    "性能", "影像", "屏幕", "续航", "系统", "外观",
]


def fetch(url: str) -> str:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        return resp.text
    except Exception as e:
        print(f"   请求失败: {url} -> {e}")
        return ""


def extract_phone_list() -> list:
    """从列表页提取全部手机产品链接"""
    html = fetch(LIST_URL)
    if not html:
        return []

    soup = BeautifulSoup(html, "lxml")
    phones = []
    seen = set()

    # 找所有 /cn/smartphones/series-xxx/yyy/ 格式的链接
    for a in soup.find_all("a", href=re.compile(r"/cn/smartphones/series-[^/]+/[^/]+/$")):
        href = a.get("href", "")
        if any(k in href.lower() for k in EXCLUDE_KEYWORDS):
            continue

        # 解析 series 和 model
        m = re.search(r"/series-([^/]+)/([^/]+)/$", href)
        if not m:
            continue
        series = m.group(1)
        model = m.group(2)

        # 跳过 3D 页面
        if model == "3d":
            continue

        # 构造完整 URL
        if href.startswith("//"):
            url = "https:" + href
        elif href.startswith("/"):
            url = "https://www.oppo.com" + href
        else:
            url = href

        if url in seen:
            continue
        seen.add(url)

        # 从链接文本或 alt 获取名称
        name = a.get_text(strip=True)
        if not name:
            img = a.find("img")
            if img:
                name = img.get("alt", "")
        if not name:
            name = model.replace("-", " ").title()

        phones.append({
            "name": name,
            "series": series,
            "model": model,
            "url": url,
            "specs_url": SPECS_URL_TEMPLATE.format(series=series, model=model),
        })

    return phones


def parse_specs_from_html(html: str) -> dict:
    """从规格页 HTML 解析规格参数（按分组组织）"""
    soup = BeautifulSoup(html, "lxml")

    # 找到规格主容器
    container = soup.select_one("div.cmp_product-params, div.root.responsivegrid")
    if not container:
        return {}

    text = container.get_text("\n", strip=True)
    lines = [l.strip() for l in text.split("\n") if l.strip()]

    specs = {}
    current_group = "基本信息"
    i = 0

    # 跳过头部（产品名称、颜色、入网型号等）
    while i < len(lines):
        line = lines[i]
        # 找到第一个规格分组标题
        if any(g in line for g in SPEC_GROUPS) and len(line) < 15:
            break
        i += 1

    while i < len(lines):
        line = lines[i]

        # 跳过脚注编号（单独的数字）
        if re.match(r"^\d+$", line):
            i += 1
            continue

        # 跳过产品对比等无关内容
        if line in ["产品对比", "入网型号"] or line.startswith("入网型号"):
            i += 1
            continue

        # 判断是否为分组标题
        is_group = False
        for g in SPEC_GROUPS:
            if g in line and len(line) < 15:
                current_group = line
                if current_group not in specs:
                    specs[current_group] = {}
                is_group = True
                break

        if is_group:
            i += 1
            continue

        # 解析键值对（键 + 值连续两行）
        if i + 1 < len(lines):
            key = line
            value = lines[i + 1]

            # 跳过值是分组标题的情况
            if any(g in value for g in SPEC_GROUPS) and len(value) < 15:
                i += 1
                continue

            # 跳过值是脚注编号的情况
            if re.match(r"^\d+$", value):
                i += 1
                continue

            # 合并多行值（如果下一行不是键也不是分组标题）
            j = i + 2
            while j < len(lines):
                next_line = lines[j]
                if re.match(r"^\d+$", next_line):
                    j += 1
                    continue
                if any(g in next_line for g in SPEC_GROUPS) and len(next_line) < 15:
                    break
                # 判断是否是新的键（短文本，通常 2-10 个字）
                if len(next_line) < 15 and not re.search(r"[，。；：]", next_line):
                    break
                value += " " + next_line
                j += 1

            if key and value and len(key) < 30:
                if current_group not in specs:
                    specs[current_group] = {}
                if key not in specs[current_group]:
                    specs[current_group][key] = value

            i = j if j > i + 2 else i + 2
        else:
            i += 1

    return specs


def main():
    print("=" * 60)
    print("  OPPO 手机全系列规格爬虫（完整版）")
    print("=" * 60)

    # 第1步：从列表页提取全部手机
    print("\n[1/3] 从列表页提取全部手机机型...")
    phones = extract_phone_list()
    print(f"  共发现 {len(phones)} 款手机:")

    # 按系列分组显示
    series_map = {}
    for phone in phones:
        s = phone["series"]
        if s not in series_map:
            series_map[s] = []
        series_map[s].append(phone)

    for series, items in series_map.items():
        print(f"\n  [{series}] ({len(items)} 款):")
        for phone in items:
            print(f"    {phone['name']:35s} | {phone['model']}")

    # 第2步：逐个采集规格页
    print(f"\n[2/3] 采集规格参数（每款约 1-2 秒，共 {len(phones)} 款）...")
    results = []
    success_count = 0
    os.makedirs("oppo_data", exist_ok=True)
    temp_path = "oppo_data/oppo_phones_temp.json"

    for i, phone in enumerate(phones, 1):
        print(f"  [{i}/{len(phones)}] {phone['name']}", end=" ", flush=True)
        try:
            html = fetch(phone["specs_url"])
            if html:
                specs = parse_specs_from_html(html)
            else:
                specs = {}
        except Exception as e:
            print(f"-> 异常: {e}")
            specs = {}

        n_fields = sum(len(v) for v in specs.values())
        status = "OK" if n_fields > 0 else "无规格页"
        if n_fields > 0:
            success_count += 1
        print(f"-> {n_fields} 字段 [{status}]")

        results.append({
            "name": phone["name"],
            "series": phone["series"],
            "model": phone["model"],
            "url": phone["url"],
            "specs_url": phone["specs_url"],
            "price": "",  # 价格待补充
            "crawl_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source": "OPPO 官网",
            "specs": specs,
        })

        # 增量保存
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

        time.sleep(REQUEST_INTERVAL)

    # 第3步：保存数据
    print(f"\n[3/3] 保存数据...")
    json_path = "oppo_data/oppo_phones.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"  JSON: {json_path} ({len(results)} 条, {success_count} 款有规格)")

    if results:
        csv_path = "oppo_data/oppo_phones.csv"
        flat_list = []
        all_keys = ["name", "series", "model", "specs_url"]
        for phone in results:
            flat = {
                "name": phone["name"],
                "series": phone["series"],
                "model": phone["model"],
                "specs_url": phone["specs_url"],
            }
            for group, pairs in phone["specs"].items():
                for k, v in pairs.items():
                    key = f"{group}_{k}"
                    flat[key] = v
                    if key not in all_keys:
                        all_keys.append(key)
            flat_list.append(flat)

        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=all_keys, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(flat_list)
        print(f"  CSV:  {csv_path}")

    # 清理临时文件
    if os.path.exists(temp_path):
        os.remove(temp_path)

    print(f"\n完成！共 {len(results)} 款手机，{success_count} 款有完整规格。")


if __name__ == "__main__":
    main()

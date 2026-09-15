# -*- coding: utf-8 -*-
"""
小米官网手机全系列规格 + 价格爬虫（完整版）

数据来源：
  - 列表页: https://www.mi.com/shop/search?keyword=手机 （3页，约50款）
  - 规格页: https://www.mi.com/prod/{slug}/specs  （Playwright 渲染提取完整规格）

安装依赖：
  pip install playwright requests beautifulsoup4
  playwright install chromium

输出：
  - xiaomi_data/xiaomi_phones.json  （完整结构化数据）
  - xiaomi_data/xiaomi_phones.csv   （扁平化，可直接入库）
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

SEARCH_URL = "https://www.mi.com/shop/search?keyword=%E6%89%8B%E6%9C%BA"
SPECS_URL_TEMPLATE = "https://www.mi.com/prod/{slug}/specs"
REQUEST_INTERVAL = 1.0
TIMEOUT = 20


def is_phone(name: str) -> bool:
    """判断是否为手机产品"""
    nl = name.lower()
    if not any(k in nl for k in ["redmi", "xiaomi", "红米", "小米"]):
        return False
    if not re.search(r"\d", name):
        return False
    exclude = [
        "电视", "笔记本", "平板", "音箱", "路由器", "空调", "冰箱", "洗衣机",
        "book", "pad", "tv", "buds", "watch", "band", "sound", "glass",
        "显示器", "投影仪", "扫地机", "吸尘器", "净化器", "电饭煲", "水壶",
        "风扇", "门锁", "摄像头", "充电器", "充电宝", "手机壳", "贴膜",
        "眼镜", "牙刷", "剃须刀", "体重秤", "打印机", "耳夹", "头戴", "骨传导",
        "性价比爆款", "巨幕影院", "游戏高刷", "全屋路由", "路由", "米家",
        "巨省电", "微冰鲜", "洗烘", "对开", "立式", "新1级",
    ]
    return not any(k in nl for k in exclude)


def clean_model_name(name: str) -> str:
    """去掉存储配置，只保留型号名称"""
    name = re.sub(r"\s*\d+GB\+\d+GB\s*$", "", name)
    name = re.sub(r"\s*\d+GB\s*$", "", name)
    return name.strip()


def parse_price(price_str: str) -> str:
    """从价格字符串提取最低价格数字"""
    prices = re.findall(r"(\d+)\s*元", price_str)
    if prices:
        return min(prices, key=int)
    return price_str


def name_to_slug(name: str) -> str:
    """从产品名称生成 slug"""
    slug = name.lower().strip()
    slug = slug.replace("至尊版", "ultimate")
    slug = slug.replace("pro+", "pro-plus")
    slug = slug.replace("+", "plus")
    slug = slug.replace(" ", "-")
    slug = re.sub(r"[^a-z0-9\-]", "", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug


def get_slug_candidates(name: str) -> list:
    """生成 slug 候选列表，按优先级排序"""
    base = name_to_slug(name)
    candidates = [base]

    if "至尊" in name:
        candidates.append(base.replace("-ultimate", "-pro-max"))
        candidates.append(base.replace("-ultimate", "-pro"))
        candidates.append(re.sub(r"-ultimate$", "", base))

    if base.endswith("-5g"):
        candidates.append(base[:-3])

    candidates.append(re.sub(r"-(ultimate|pro-max|pro|max)$", "", base))

    seen = set()
    result = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            result.append(c)
    return result


def extract_product_list_from_search(page) -> list:
    """从搜索结果页提取手机产品列表（用 Playwright 渲染后的页面）"""
    products = page.evaluate('''() => {
        const items = [];
        document.querySelectorAll('.goods-item').forEach(card => {
            const title = card.querySelector('.title');
            const price = card.querySelector('.price');
            const link = card.querySelector('a[href*="product_id"]');
            if (title && price) {
                items.push({
                    name: title.innerText.trim(),
                    price: price.innerText.trim(),
                    href: link ? link.href : '',
                    pid: link ? (link.href.match(/product_id=(\\d+)/) || ['',''])[1] : ''
                });
            }
        });
        return items;
    }''')

    phones = []
    seen_models = set()
    for prod in products:
        if is_phone(prod["name"]):
            model = clean_model_name(prod["name"])
            if model not in seen_models:
                seen_models.add(model)
                phones.append({
                    "name": model,
                    "original_name": prod["name"],
                    "price": parse_price(prod["price"]),
                    "original_price": prod["price"],
                    "product_id": prod["pid"],
                    "slug": name_to_slug(model),
                })
    return phones


def parse_specs_from_text(text: str) -> dict:
    """从渲染后的页面文本中解析规格参数（按分组组织）"""
    lines = [l.strip() for l in text.split("\n") if l.strip()]

    start_idx = 0
    for i, l in enumerate(lines):
        if "规格参数" in l or "基本信息" in l or "外观尺寸" in l:
            start_idx = i
            break

    specs = {}
    current_group = "基本信息"

    for i in range(start_idx, len(lines)):
        line = lines[i]

        if line.startswith("*") or line.startswith("注：") or line.startswith("注:"):
            continue
        if any(k in line for k in ["选购指南", "服务中心", "关于小米", "关注我们", "小米官网", "小米商城"]):
            break
        if len(line) < 2:
            continue

        has_colon = "：" in line or ":" in line
        if not has_colon and re.search(r"[\u4e00-\u9fa5]", line) and len(line) < 15:
            current_group = line
            if current_group not in specs:
                specs[current_group] = {}
            continue

        if has_colon:
            if "：" in line:
                key, _, value = line.partition("：")
            else:
                key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            if key and value and len(key) < 30:
                if current_group not in specs:
                    specs[current_group] = {}
                if key not in specs[current_group]:
                    specs[current_group][key] = value

    return specs


def crawl_specs_with_playwright(name: str, slug: str, page) -> dict:
    """用 Playwright 渲染规格页，提取规格参数（支持 slug 回退）"""
    candidates = get_slug_candidates(name)
    if slug not in candidates:
        candidates.insert(0, slug)

    for try_slug in candidates:
        url = SPECS_URL_TEMPLATE.format(slug=try_slug)
        try:
            page.goto(url, wait_until="networkidle", timeout=30000)
            time.sleep(2)

            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(1)
            page.evaluate("window.scrollTo(0, 0)")
            time.sleep(1)

            html = page.content()
            soup = BeautifulSoup(html, "lxml")
            for s in soup.find_all(["script", "style"]):
                s.decompose()
            text = soup.get_text("\n", strip=True)

            specs = parse_specs_from_text(text)
            if specs and sum(len(v) for v in specs.values()) > 0:
                return specs
        except Exception as e:
            print(f"\n   尝试 slug '{try_slug}' 失败: {e}")
            continue

    return {}


def main():
    print("=" * 60)
    print("  小米手机全系列规格 + 价格爬虫（完整版）")
    print("=" * 60)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        launch_kwargs = {
            "headless": True,
            "args": ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
        }
        chromium_path = os.environ.get("PLAYWRIGHT_CHROMIUM_PATH", "")
        if chromium_path and os.path.exists(chromium_path):
            launch_kwargs["executable_path"] = chromium_path
        browser = p.chromium.launch(**launch_kwargs)
        page = browser.new_page(viewport={"width": 1280, "height": 900})

        # 第1步：从搜索页提取全部手机列表（3页）
        print("\n[1/3] 从搜索结果页提取手机列表（3页）...")
        all_phones = []
        seen_models = set()

        for page_num in range(1, 4):
            if page_num == 1:
                page.goto(SEARCH_URL, wait_until="networkidle", timeout=30000)
            else:
                try:
                    page.click(f'.mi-pagenav .numbers:has-text("{page_num}")', timeout=5000)
                    time.sleep(3)
                except Exception as e:
                    print(f"  点击第{page_num}页失败: {e}")
                    continue

            time.sleep(2)
            for _ in range(5):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(1)

            phones = extract_product_list_from_search(page)
            new_count = 0
            for phone in phones:
                if phone["name"] not in seen_models:
                    seen_models.add(phone["name"])
                    all_phones.append(phone)
                    new_count += 1

            print(f"  第{page_num}页: 新增 {new_count} 款, 累计 {len(all_phones)} 款")

        print(f"\n  共发现 {len(all_phones)} 款手机:")
        for i, phone in enumerate(all_phones, 1):
            print(f"    {i:2d}. {phone['name']:30s} | ￥{phone['price']:>6s} | slug={phone['slug']}")

        # 第2步：逐个渲染规格页
        print(f"\n[2/3] 渲染规格页提取参数（每款约 5-8 秒，共 {len(all_phones)} 款）...")
        results = []
        success_count = 0
        os.makedirs("xiaomi_data", exist_ok=True)
        temp_path = "xiaomi_data/xiaomi_phones_temp.json"

        for i, phone in enumerate(all_phones, 1):
            print(f"  [{i}/{len(all_phones)}] {phone['name']}", end=" ", flush=True)
            try:
                specs = crawl_specs_with_playwright(phone["name"], phone["slug"], page)
            except Exception as e:
                print(f"-> 采集异常: {e}")
                specs = {}

            n_fields = sum(len(v) for v in specs.values())
            status = "OK" if n_fields > 0 else "无规格页"
            if n_fields > 0:
                success_count += 1
            print(f"-> {n_fields} 字段 [{status}]")

            results.append({
                "name": phone["name"],
                "original_name": phone["original_name"],
                "slug": phone["slug"],
                "product_id": phone["product_id"],
                "price": phone["price"],
                "original_price": phone["original_price"],
                "url": SPECS_URL_TEMPLATE.format(slug=phone["slug"]),
                "crawl_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "source": "小米商城",
                "specs": specs,
            })

            # 增量保存，避免中途失败丢失数据
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=2)

            time.sleep(REQUEST_INTERVAL)

        browser.close()

    # 第3步：保存数据
    print(f"\n[3/3] 保存数据...")
    os.makedirs("xiaomi_data", exist_ok=True)

    json_path = "xiaomi_data/xiaomi_phones.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"  JSON: {json_path} ({len(results)} 条, {success_count} 款有规格)")

    if results:
        csv_path = "xiaomi_data/xiaomi_phones.csv"
        flat_list = []
        all_keys = ["name", "price", "product_id", "url"]
        for phone in results:
            flat = {
                "name": phone["name"],
                "price": phone["price"],
                "product_id": phone["product_id"],
                "url": phone["url"],
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

    print(f"\n完成！共 {len(results)} 款手机，{success_count} 款有完整规格。")


if __name__ == "__main__":
    main()

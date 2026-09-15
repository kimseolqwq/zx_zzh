#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
荣耀手机全系列爬虫
从荣耀官网规格参数页采集手机规格参数，从荣耀商城采集价格
数据来源:
  - 规格参数: https://www.honor.com/cn/phones/{slug}/spec/
  - 价格: https://www.honor.com/cn/shop/v/search?categoryId=36 (Playwright渲染)

使用方法:
  python honor_crawler.py          # 采集规格参数
  python honor_crawler.py price    # 采集价格（需要Playwright）
"""

import requests
import json
import time
import re
import csv
import os
import sys
from bs4 import BeautifulSoup

# 请求头
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def get_group(param_name):
    """根据参数名前缀判断分组"""
    name = param_name.strip()
    if name in ['颜色']:
        return '颜色'
    elif name in ['长度', '宽度', '厚度', '重量']:
        return '尺寸与重量'
    elif name.startswith('屏幕') or name in ['屏幕触控']:
        return '屏幕'
    elif name.startswith('CPU') or name == 'GPU' or name.startswith('处理器'):
        return '处理器'
    elif name == '操作系统':
        return '系统'
    elif name in ['键盘类型', '特色功能', '安全功能', '防尘抗水']:
        return '其他功能'
    elif name.startswith('后置摄像头') or name in ['拍摄功能', '防抖模式']:
        return '后置摄像头'
    elif name.startswith('前置摄像头') or name == '人脸识别':
        return '前置摄像头'
    elif name.startswith('电池') or name in ['有线快充', '无线充电', '智能充电模式']:
        return '电池'
    elif name.startswith('网络') or name.startswith('SIM') or name == '蜂窝网络':
        return '蜂窝网络'
    elif name in ['WLAN功能', '蓝牙', '其他', '定位', '连接与定位']:
        return '连接与定位'
    elif name in ['传感器', 'NFC']:
        return '传感器'
    elif name in ['视频文件格式', '音频文件格式', '音效', '立体声扬声器', '拾音功能', '多媒体']:
        return '多媒体'
    elif name == '包装清单':
        return '包装清单'
    elif name in ['运行内存', '机身存储', '存储']:
        return '存储'
    else:
        return '其他'


def get_phone_list():
    """从荣耀官网获取手机列表"""
    url = "https://www.honor.com/cn/phones/"
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.encoding = resp.apparent_encoding
    html = resp.text
    soup = BeautifulSoup(html, 'lxml')

    phones = {}
    for a in soup.find_all('a', href=True):
        href = a['href']
        text = a.get_text(strip=True)
        if re.match(r'^/cn/phones/honor-[a-z0-9-]+/?$', href) and text:
            name = re.sub(r'^New', '', text).strip()
            slug = href.rstrip('/').split('/')[-1]
            if slug not in phones and len(name) < 50:
                series = '其他'
                if 'magic' in slug:
                    series = 'Magic系列'
                elif 'play' in slug:
                    series = 'Play系列'
                elif 'power' in slug:
                    series = 'Power系列'
                elif 'win' in slug:
                    series = 'WIN系列'
                elif re.match(r'honor-x\d', slug):
                    series = 'X系列'
                elif 'robot' in slug:
                    series = 'Robot Phone系列'
                elif re.match(r'honor-\d+', slug):
                    series = '数字系列'
                elif 'changwan' in slug:
                    series = '畅玩系列'
                elif 'gt' in slug:
                    series = 'GT系列'
                elif 'v-purse' in slug:
                    series = 'V系列'

                phones[slug] = {
                    'name': name,
                    'slug': slug,
                    'series': series,
                    'url': f"https://www.honor.com{href}",
                    'spec_url': f"https://www.honor.com/cn/phones/{slug}/spec/",
                }

    return list(phones.values())


def get_spec_page(slug):
    """获取规格参数页 HTML"""
    url = f"https://www.honor.com/cn/phones/{slug}/spec/"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.encoding = resp.apparent_encoding
        if resp.status_code == 200 and len(resp.text) > 10000:
            if '页面找不到' in resp.text:
                return None
            return resp.text
        else:
            return None
    except Exception as e:
        print(f"    获取页面失败: {e}")
        return None


def parse_specs(html):
    """解析规格参数页 HTML（根据参数名前缀分组）"""
    soup = BeautifulSoup(html, 'lxml')
    specs = {}

    spec_items = soup.select('.products-spec-component-list-item-right-item-list-item')
    for item in spec_items:
        h3 = item.find('h3')
        if not h3:
            continue
        param_name = h3.get_text(strip=True)

        desc = item.select_one('.products-spec-component-list-item-desc-container')
        if not desc:
            continue

        value_containers = desc.select('.cc-product-content-container')
        values = [vc.get('data-value', '').strip() for vc in value_containers if vc.get('data-value')]
        if values:
            group = get_group(param_name)
            if group not in specs:
                specs[group] = {}
            specs[group][param_name] = ' '.join(values)

    return specs


def crawl_specs():
    """采集所有手机规格参数"""
    print("=" * 60)
    print("荣耀手机全系列爬虫 - 规格参数采集")
    print("=" * 60)

    print("\n[1/2] 获取手机列表...")
    phones = get_phone_list()
    print(f"  共找到 {len(phones)} 款手机")

    series_count = {}
    for p in phones:
        s = p['series']
        series_count[s] = series_count.get(s, 0) + 1
    print(f"\n  按系列统计:")
    for s, c in sorted(series_count.items()):
        print(f"    {s}: {c} 款")

    print(f"\n[2/2] 采集规格参数（共 {len(phones)} 款）...")

    output_file = 'data/honor_phones.json'
    results = []
    if os.path.exists(output_file):
        results = json.load(open(output_file, encoding='utf-8'))
        print(f"  已采集 {len(results)} 款，继续采集...")
    collected_slugs = {r['slug'] for r in results}

    failed = []

    for idx, phone in enumerate(phones, 1):
        slug = phone['slug']
        name = phone['name']

        if slug in collected_slugs:
            print(f"\n[{idx}/{len(phones)}] {name} - 已采集，跳过")
            continue

        print(f"\n[{idx}/{len(phones)}] {name} ({slug})")

        html = get_spec_page(slug)
        if not html:
            print(f"  失败: 无法获取规格参数页（可能已下架）")
            failed.append((name, slug, "无法获取规格参数页"))
            continue

        specs = parse_specs(html)
        total_fields = sum(len(v) for v in specs.values())

        if total_fields == 0:
            print(f"  警告: 未解析到规格参数（老款机型页面结构不同）")
            failed.append((name, slug, "未解析到规格参数"))

        result = {
            'name': name,
            'series': phone['series'],
            'slug': slug,
            'price': None,
            'url': phone['url'],
            'spec_url': phone['spec_url'],
            'specs': specs,
            'spec_count': total_fields,
            'crawl_time': time.strftime("%Y-%m-%d %H:%M:%S"),
            'source': '荣耀官网',
        }
        results.append(result)
        collected_slugs.add(slug)

        print(f"  成功: {total_fields} 个规格字段")

        if idx % 10 == 0:
            save_results(results)
            print(f"  (已保存中间结果，共 {len(results)} 款)")

        time.sleep(0.5)

    save_results(results)

    print(f"\n{'='*60}")
    print(f"规格参数采集完成！")
    print(f"  成功: {len(results)} 款")
    print(f"  失败: {len(failed)} 款")
    if failed:
        print(f"  失败列表:")
        for name, slug, reason in failed[:20]:
            print(f"    - {name} ({slug}): {reason}")

    total_specs = sum(r['spec_count'] for r in results)
    has_specs = [r for r in results if r['spec_count'] > 0]
    print(f"\n  总规格字段数: {total_specs}")
    print(f"  有规格机型: {len(has_specs)} 款")
    if has_specs:
        print(f"  平均每款规格数: {total_specs / len(has_specs):.1f}")

    return results


def crawl_prices():
    """从荣耀商城采集价格（需要Playwright）"""
    print("=" * 60)
    print("荣耀手机价格采集（需要Playwright）")
    print("=" * 60)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("错误: 未安装playwright，请运行: pip install playwright && playwright install chromium")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage']
        )
        page = browser.new_page()

        url = "https://www.honor.com/cn/shop/v/search?categoryId=36"
        print(f"\n访问手机分类页: {url}")
        page.goto(url, timeout=30000)
        time.sleep(3)

        for i in range(10):
            page.evaluate("window.scrollBy(0, 600)")
            time.sleep(0.5)

        links = page.query_selector_all('a[href*="/cn/shop/product/"]')
        print(f"找到 {len(links)} 个产品链接")

        products = []
        seen = set()
        for link in links:
            href = link.get_attribute('href') or ''
            text = link.inner_text().strip()

            if not text or len(text) > 200:
                continue

            lines = [l.strip() for l in text.split('\n') if l.strip()]
            if not lines:
                continue

            product_name = lines[0]

            if any(k in product_name for k in ['平板', '手表', '耳机', '笔记本', '电脑', '手环', '充电器', '数据线', '键盘', '鼠标', '路由器', '智慧屏', '电视', '音箱', '摄像头', '门锁', '秤', '眼镜', '笔', '保护壳', '贴膜', '散热器']):
                continue

            price = None
            for line in lines:
                m = re.search(r'预估到手价[¥￥]\s*([\d,]+)', line)
                if m:
                    price = int(m.group(1).replace(',', ''))
                    break
                m = re.search(r'[¥￥]\s*([\d,]+)', line)
                if m:
                    price = int(m.group(1).replace(',', ''))
                    break

            if product_name not in seen and price:
                seen.add(product_name)
                products.append({
                    'name': product_name,
                    'price': price,
                    'url': href,
                })
                print(f"  {product_name}: ¥{price}")

        browser.close()

    print(f"\n共获取 {len(products)} 款手机价格")

    output_file = 'rongyao_data/honor_phones.json'
    if os.path.exists(output_file):
        phones = json.load(open(output_file, encoding='utf-8'))

        price_map = {}
        for p in products:
            name = p['name'].replace(' ', '').replace('荣耀', '')
            price_map[name] = p['price']
            price_map[p['name']] = p['price']

        matched = 0
        for phone in phones:
            name = phone['name'].replace(' ', '').replace('荣耀', '')
            if name in price_map:
                phone['price'] = price_map[name]
                matched += 1
            elif phone['name'] in price_map:
                phone['price'] = price_map[phone['name']]
                matched += 1
            else:
                for key in price_map:
                    if key in name or name in key:
                        phone['price'] = price_map[key]
                        matched += 1
                        break

        print(f"匹配到价格: {matched} 款")
        save_results(phones)
        print("价格已更新到数据文件")
    else:
        price_file = 'rongyao_data/honor_prices.json'
        with open(price_file, 'w', encoding='utf-8') as f:
            json.dump(products, f, ensure_ascii=False, indent=2)
        print(f"价格数据已保存: {price_file}")

    return products


def save_results(results):
    """保存结果为 JSON 和 CSV"""
    os.makedirs('rongyao_data', exist_ok=True)

    json_path = 'rongyao_data/honor_phones.json'
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"  JSON 已保存: {json_path}")

    csv_path = 'rongyao_data/honor_phones.csv'
    all_fields = set()
    for r in results:
        for group, params in r['specs'].items():
            for param in params:
                all_fields.add(f"{group}_{param}")

    fieldnames = ['name', 'series', 'price', 'slug', 'url', 'spec_count'] + sorted(all_fields)

    with open(csv_path, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            row = {
                'name': r['name'],
                'series': r['series'],
                'price': r['price'],
                'slug': r['slug'],
                'url': r['url'],
                'spec_count': r['spec_count'],
            }
            for group, params in r['specs'].items():
                for param, value in params.items():
                    row[f"{group}_{param}"] = value
            writer.writerow(row)
    print(f"  CSV 已保存: {csv_path} ({len(fieldnames)}列)")


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'price':
        crawl_prices()
    else:
        crawl_specs()

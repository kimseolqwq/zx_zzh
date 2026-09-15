#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vivo 手机全系列爬虫（商城API版）
从 vivo 商城 API 采集全部手机的规格参数和价格
数据来源: https://shop.vivo.com.cn/
"""

import requests
import json
import time
import re
import csv
import os

# 请求头
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://shop.vivo.com.cn/",
    "Origin": "https://shop.vivo.com.cn",
}

# 商城分类列表
CATEGORIES = {
    1: '智能手机',
    527: 'iQOO旗舰',
    57: 'vivo X',
    531: 'vivo S',
    7: 'vivo Y',
    673: 'iQOO Neo',
    674: 'iQOO Z',
}

# 非手机产品关键词
NON_PHONE_KEYWORDS = [
    '耳机', '平板', '手表', '充电', '保护壳', '保护膜', '数据线', '充电宝',
    '音箱', '手环', '眼镜', '键盘', '笔', '鼠标', '散热', '影像套装',
    'LUT', '办公套件', '打印机', '风扇', '插座', '洗衣', '水杯', '四件套',
    '洗衣液', '洗洁精', '保温箱', '除螨', '冰箱', '纸巾', '体重秤', '体脂秤',
    '网卡', '清洁', '吧唧', '收纳包', '鼠标垫', '游戏', '存储卡', 'U盘',
    '保护套', '钢化膜', '闪充', '充电器', '蓝牙', 'TWS', 'WATCH', 'Pad',
    'Buds', 'Air', '套装', '配件', '腕带', '表带', '充电线', '转接头',
    '手机壳', '手机膜', '支架', '挂绳', '贴纸', '海报', '画册', '周边',
]


def get_product_list():
    """从商城API获取全部手机产品列表"""
    all_products = {}  # spuId -> product info

    for cat_id, cat_name in CATEGORIES.items():
        print(f"  获取分类: {cat_name} (list-{cat_id})...")
        page_num = 1
        while True:
            t = int(time.time() * 1000)
            url = f"https://shop.vivo.com.cn/api/v1/prodList/list-{cat_id}?pageNum={page_num}&pageSize=20&t={t}"
            try:
                resp = requests.get(url, headers=HEADERS, timeout=15)
                data = resp.json()
                if data.get('code') != 0:
                    break

                data_list = data.get('data', {}).get('dataList', [])
                total_page = data.get('data', {}).get('totalPage', 0)

                if not data_list:
                    break

                for item in data_list:
                    spu_id = item.get('relaSpuId')
                    sku_id = item.get('id')
                    sku_name = item.get('skuName', '')
                    sale_price = item.get('salePrice', 0)
                    brief = item.get('brief', '')

                    if spu_id and spu_id not in all_products:
                        # 清理产品名称
                        name = re.sub(r'\d+GB\+\d+GB', '', sku_name)
                        name = re.sub(r'\d+GB', '', name)
                        name = re.sub(r'\s+', ' ', name).strip()

                        all_products[spu_id] = {
                            'spu_id': spu_id,
                            'sku_id': sku_id,
                            'name': name,
                            'sku_name': sku_name,
                            'category': cat_name,
                            'price': sale_price,
                            'brief': brief,
                        }

                if total_page and page_num >= total_page:
                    break
                page_num += 1
                time.sleep(0.3)
            except Exception as e:
                print(f"    第{page_num}页失败: {e}")
                break

    # 过滤手机产品
    phones = {}
    for spu_id, product in all_products.items():
        name = product['name']
        if not any(kw in name for kw in NON_PHONE_KEYWORDS):
            phones[spu_id] = product

    print(f"  共获取 {len(all_products)} 款产品，其中手机 {len(phones)} 款")
    return phones


def get_sku_id(spu_id):
    """获取产品的默认skuId"""
    t = int(time.time() * 1000)
    url = f"https://shop.vivo.com.cn/api/v1/product/getInfo?spuId={spu_id}&t={t}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        data = resp.json()
        if data.get('code') == 0:
            default_sku = data.get('data', {}).get('defaultSkuId')
            if default_sku:
                return default_sku
            # 从 specItem 中取第一个
            spec_item = data.get('data', {}).get('specItem', {})
            sku_list = spec_item.get('skuSpecList', [])
            if sku_list:
                return sku_list[0].get('skuId')
    except Exception as e:
        print(f"    获取skuId失败: {e}")
    return None


def get_product_detail(spu_id, sku_id):
    """获取产品详情（含规格参数）"""
    t = int(time.time() * 1000)
    url = f"https://shop.vivo.com.cn/api/v1/product/getDetail?spuId={spu_id}&typeId=1&skuId={sku_id}&needSurfRecord=true&t={t}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        data = resp.json()
        if data.get('code') == 0:
            detail = data.get('data', {})
            sku_data = detail.get(str(sku_id), {})
            if not sku_data:
                # 尝试其他 key
                for k, v in detail.items():
                    if isinstance(v, dict) and 'parameters' in v:
                        sku_data = v
                        break
            return sku_data
    except Exception as e:
        print(f"    获取详情失败: {e}")
    return None


def parse_specs(sku_data):
    """解析规格参数"""
    specs = {}
    parameters = sku_data.get('parameters', [])

    for param_group in parameters:
        group_name = param_group.get('name', '其他')
        items = param_group.get('items', [])
        if group_name not in specs:
            specs[group_name] = {}
        for item in items:
            name = item.get('name', '')
            value = item.get('value', '')
            if name and value:
                # 清理 HTML 标签
                value = re.sub(r'<[^>]+>', ' ', value)
                value = re.sub(r'\s+', ' ', value).strip()
                specs[group_name][name] = value

    return specs


def extract_price(sku_data, product):
    """提取价格"""
    # 优先从详情中获取
    sale_price = sku_data.get('salePrice')
    if sale_price:
        return str(sale_price)
    # 从产品列表中获取
    if product.get('price'):
        return str(product['price'])
    return None


def crawl_all():
    """采集所有手机"""
    print("=" * 60)
    print("vivo 手机全系列爬虫（商城API版）")
    print("=" * 60)

    # 1. 获取产品列表
    print("\n[1/2] 获取产品列表...")
    phones = get_product_list()

    if not phones:
        print("未获取到产品列表，退出")
        return

    # 2. 逐个获取规格参数
    print(f"\n[2/2] 采集规格参数（共 {len(phones)} 款）...")
    results = []
    failed = []

    for idx, (spu_id, product) in enumerate(phones.items(), 1):
        name = product['name']
        print(f"\n[{idx}/{len(phones)}] {name} (spuId={spu_id})")

        # 获取 skuId
        sku_id = product.get('sku_id')
        if not sku_id:
            sku_id = get_sku_id(spu_id)
        if not sku_id:
            print(f"  失败: 无法获取skuId")
            failed.append((name, spu_id, "无法获取skuId"))
            continue

        # 获取产品详情
        sku_data = get_product_detail(spu_id, sku_id)
        if not sku_data:
            print(f"  失败: 无法获取产品详情")
            failed.append((name, spu_id, "无法获取产品详情"))
            continue

        # 解析规格参数
        specs = parse_specs(sku_data)
        total_fields = sum(len(v) for v in specs.values())

        # 提取价格
        price = extract_price(sku_data, product)

        # 提取产品名称
        spu_name = sku_data.get('spuName', product['name'])
        sku_name = sku_data.get('skuName', product['sku_name'])

        result = {
            'name': spu_name or product['name'],
            'sku_name': sku_name,
            'series': product['category'],
            'spu_id': spu_id,
            'sku_id': sku_id,
            'price': price,
            'brief': product.get('brief', ''),
            'url': f"https://shop.vivo.com.cn/product/{spu_id}",
            'specs': specs,
            'spec_count': total_fields,
            'crawl_time': time.strftime("%Y-%m-%d %H:%M:%S"),
            'source': 'vivo商城',
        }
        results.append(result)

        print(f"  成功: {total_fields} 个规格字段, 价格: {price or '未知'}")

        # 保存中间结果
        if idx % 5 == 0:
            save_results(results)
            print(f"  (已保存中间结果，共 {len(results)} 款)")

        time.sleep(0.5)

    # 保存最终结果
    save_results(results)

    # 打印统计
    print(f"\n{'='*60}")
    print(f"采集完成！")
    print(f"  成功: {len(results)} 款")
    print(f"  失败: {len(failed)} 款")
    if failed:
        print(f"  失败列表:")
        for name, spu_id, reason in failed:
            print(f"    - {name} ({spu_id}): {reason}")

    # 按系列统计
    series_count = {}
    for r in results:
        s = r['series']
        series_count[s] = series_count.get(s, 0) + 1
    print(f"\n  按系列统计:")
    for s, c in sorted(series_count.items()):
        print(f"    {s}: {c} 款")

    # 总规格字段数
    total_specs = sum(r['spec_count'] for r in results)
    print(f"\n  总规格字段数: {total_specs}")
    print(f"  有价格的机型: {sum(1 for r in results if r.get('price'))}")

    return results


def save_results(results):
    """保存结果为 JSON 和 CSV"""
    os.makedirs('vivo_data', exist_ok=True)

    # 保存 JSON
    json_path = 'vivo_data/vivo_phones_full.json'
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"  JSON 已保存: {json_path}")

    # 保存 CSV（扁平化）
    csv_path = 'vivo_data/vivo_phones_full.csv'
    all_fields = set()
    for r in results:
        for group, params in r['specs'].items():
            for param in params:
                all_fields.add(f"{group}_{param}")

    fieldnames = ['name', 'series', 'price', 'spu_id', 'url', 'spec_count', 'brief'] + sorted(all_fields)

    with open(csv_path, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            row = {
                'name': r['name'],
                'series': r['series'],
                'price': r['price'],
                'spu_id': r['spu_id'],
                'url': r['url'],
                'spec_count': r['spec_count'],
                'brief': r.get('brief', ''),
            }
            for group, params in r['specs'].items():
                for param, value in params.items():
                    row[f"{group}_{param}"] = value
            writer.writerow(row)
    print(f"  CSV 已保存: {csv_path}")


if __name__ == '__main__':
    crawl_all()

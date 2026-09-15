from app.crawlers.official_specs import (
    completeness,
    extract_official_image,
    is_likely_product_image_url,
    parse_memory_variants,
    parse_official_specs,
    parse_release_date,
)
from app.crawlers.price_parser import parse_price_text
from app.crawlers.market_import import _valid_product_url
from app.crawlers.catalog_pipeline import _fallback_variants


def test_parse_official_specs() -> None:
    text = """
    处理器
    麒麟9000S
    屏幕尺寸：6.7 英寸
    OLED，最高支持 120 Hz 刷新率
    后置主摄 5000 万像素摄像头
    电池容量：5200 mAh
    充电支持 88 W
    重量：约 199 g
    厚度：8.2 mm
    分辨率 2688 × 1216 像素
    操作系统 HarmonyOS 5.0
    """
    parsed = parse_official_specs(text)
    assert parsed["cpu"] == "麒麟9000S"
    assert parsed["screen_size"] == 6.7
    assert parsed["refresh_rate"] == 120
    assert parsed["main_camera_mp"] == 50
    assert parsed["battery_mah"] == 5200
    assert completeness(parsed) >= 80


def test_price_parser_keeps_price_types_separate() -> None:
    text = "官方售价 ￥4299\n活动到手价 3999元\n国补价 3499元\n百亿补贴价 3699元"
    parsed = parse_price_text(text)
    assert parsed.regular_price == 4299
    assert parsed.public_sale_price == 3999
    assert parsed.displayed_gov_price == 3499
    assert parsed.billion_subsidy_price == 3699
    assert parsed.confidence == "high"


def test_price_parser_does_not_invent_unlabelled_price() -> None:
    parsed = parse_price_text("商品详情 4299 3999 3599")
    assert parsed.regular_price is None
    assert parsed.public_sale_price is None
    assert parsed.confidence == "low"


def test_official_image_uses_explicit_metadata_and_resolves_relative_url() -> None:
    html = '<meta property="og:image" content="/assets/phone.webp"><img src="camera-sample.jpg">'
    assert extract_official_image(html, "https://brand.example/phones/model/") == {
        "image_url": "https://brand.example/assets/phone.webp",
        "image_source_url": "https://brand.example/phones/model/",
    }


def test_official_image_rejects_logos_icons_and_svg_placeholders() -> None:
    html = '<meta property="og:image" content="/assets/logo.svg"><img alt="Test Phone" src="/images/test-phone.webp">'
    assert extract_official_image(html, "https://brand.example/phones/model/", "Test Phone") == {
        "image_url": "https://brand.example/images/test-phone.webp",
        "image_source_url": "https://brand.example/phones/model/",
    }
    assert not is_likely_product_image_url("https://brand.example/assets/logo.svg")
    assert not is_likely_product_image_url("https://brand.example/images/placeholder.png")
    assert not is_likely_product_image_url("https://images.example/assets/cn/gnb/Phone_88x88.png")
    assert not is_likely_product_image_url("https://brand.example/footer/WeChat.jpg")
    assert is_likely_product_image_url("https://brand.example/products/phone-front.webp")


def test_release_date_and_variants_require_explicit_labels() -> None:
    text = "上市时间：2025年10月\n12GB+256GB：4599.00元\n16GB + 1TB：6299.00元"
    assert parse_release_date(text).isoformat() == "2025-10-01"
    variants = parse_memory_variants(text)
    assert [(item["ram_gb"], item["storage_gb"]) for item in variants] == [(12, 256), (16, 1024)]
    assert float(variants[0]["launch_price"]) == 4599


def test_storage_only_variants_are_conservative() -> None:
    text = "运行内存（RAM）\n12 GB / 16 GB RAM\n机身内存（ROM）\n256 GB / 512 GB / 1 TB ROM"
    variants = parse_memory_variants(text)
    assert [(item["ram_gb"], item["storage_gb"]) for item in variants] == [
        (None, 256), (None, 512), (None, 1024)
    ]
    assert parse_memory_variants("电池 5120mAh，缓存提升 256%，屏幕 6.7 英寸") == []


def test_nested_official_labels_and_dual_cell_battery() -> None:
    text = "处理器\nCPU 型号\n麒麟9030S\n类型\nOLED，支持 1-120 Hz LTPO 自适应刷新率\n电池容量\n2×3500 mAh，等效 7000 mAh 电池能量"
    parsed = parse_official_specs(text)
    assert parsed["cpu"] == "麒麟9030S"
    assert parsed["refresh_rate"] == 120
    assert parsed["battery_mah"] == 7000


def test_apple_style_specs_are_parsed_from_explicit_labels() -> None:
    text = """
    尺寸与重量
    重量：
    177 克 (6.24 盎司)
    显示屏
    6.3 英寸 (对角线) OLED 全面屏
    2622 x 1206 像素分辨率，460 ppi
    ProMotion 自适应刷新率技术，最高可达 120Hz
    芯片
    A19 芯片
    4800 万像素融合式主摄
    iOS 26
    """
    parsed = parse_official_specs(text)
    assert parsed["weight_g"] == 177
    assert parsed["screen_size"] == 6.3
    assert parsed["resolution"] == "2622 x 1206 像素"
    assert parsed["refresh_rate"] == 120
    assert parsed["cpu"] == "A19 芯片"
    assert parsed["main_camera_mp"] == 48
    assert parsed["operating_system"] == "iOS 26"


def test_vivo_and_oppo_style_camera_and_charging_labels() -> None:
    text = """
    后置摄像头像素
    5000万像素蔡司云台级主摄
    刷新率
    1-120Hz
    快速充电
    最大支持：80 W 超级闪充
    """
    parsed = parse_official_specs(text)
    assert parsed["main_camera_mp"] == 50
    assert parsed["refresh_rate"] == 120
    assert parsed["charging_w"] == 80


def test_samsung_camera_label_and_implausible_values() -> None:
    valid = parse_official_specs("后置摄像头 - 分辨率 (多重)\n5000万像素+1200万像素+500万像素")
    assert valid["main_camera_mp"] == 50
    invalid = parse_official_specs("主摄 200 万像素\n电池容量 1200 mAh\n重量 65 g")
    assert invalid["main_camera_mp"] is None
    assert invalid["battery_mah"] is None
    assert invalid["weight_g"] is None


def test_fallback_variants_support_explicit_ram_storage_pairs() -> None:
    variants = _fallback_variants("12+256;12+512;1TB")
    assert [(item["ram_gb"], item["storage_gb"], item["variant_name"]) for item in variants] == [
        (12, 256, "12GB+256"),
        (12, 512, "12GB+512"),
        (None, 1024, "1TB"),
    ]


def test_shared_ram_storage_list_expands_only_explicit_options() -> None:
    text = "HUAWEI Pura X Max\n12 GB RAM + 256 GB / 512 GB ROM\n典藏版\n16 GB RAM + 512 GB / 1 TB ROM"
    variants = parse_memory_variants(text)
    assert {(item["ram_gb"], item["storage_gb"]) for item in variants} == {
        (12, 256), (12, 512), (16, 512), (16, 1024)
    }


def test_market_product_url_is_limited_to_matching_platform() -> None:
    assert _valid_product_url("jd", "https://item.jd.com/10001.html")
    assert _valid_product_url("tmall", "https://detail.tmall.com/item.htm?id=1")
    assert _valid_product_url("pdd", "https://mobile.yangkeduo.com/goods.html?goods_id=1")
    assert not _valid_product_url("jd", "https://example.com/fake")
    assert not _valid_product_url("pdd", "http://mobile.yangkeduo.com/goods.html")

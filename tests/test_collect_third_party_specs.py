from types import SimpleNamespace

from scripts.collect_third_party_specs import (
    boolean_value,
    sitemap_match_urls,
    sitemap_product_urls,
)


def phone(brand: str, model: str) -> SimpleNamespace:
    return SimpleNamespace(brand=SimpleNamespace(name=brand), model_name=model)


def test_sitemap_parser_keeps_only_product_pages() -> None:
    xml = """
    <urlset>
      <url><loc>https://www.mobiledokan.com/mobile/honor-x70</loc></url>
      <url><loc>https://www.mobiledokan.com/mobile/honor-x70/specification</loc></url>
      <url><loc>https://www.mobiledokan.com/mobile/honor-x70/gallery</loc></url>
    </urlset>
    """
    assert sitemap_product_urls(xml) == ["https://www.mobiledokan.com/mobile/honor-x70"]


def test_sitemap_match_accepts_region_suffix_but_not_adjacent_model() -> None:
    urls = [
        "https://www.mobiledokan.com/mobile/vivo-iqoo-neo10-china",
        "https://www.mobiledokan.com/mobile/vivo-iqoo-neo10-pro-china",
        "https://www.mobiledokan.com/mobile/iqoo-neo10-pro-plus-china",
    ]
    assert sitemap_match_urls(phone("vivo", "iQOO Neo10"), urls) == [
        "https://www.mobiledokan.com/mobile/vivo-iqoo-neo10-china"
    ]
    assert sitemap_match_urls(phone("vivo", "iQOO Neo10 Pro"), urls) == [
        "https://www.mobiledokan.com/mobile/vivo-iqoo-neo10-pro-china"
    ]


def test_sitemap_match_translates_chinese_edition_names() -> None:
    urls = [
        "https://www.mobiledokan.com/mobile/huawei-mate-xt-ultimate",
        "https://www.mobiledokan.com/mobile/huawei-mate-xt-ultimate-512gb",
    ]
    assert sitemap_match_urls(phone("华为", "HUAWEI Mate XT 非凡大师"), urls) == [
        "https://www.mobiledokan.com/mobile/huawei-mate-xt-ultimate"
    ]


def test_sitemap_match_does_not_ignore_plus_suffix() -> None:
    urls = [
        "https://www.mobiledokan.com/mobile/huawei-mate-70-pro",
        "https://www.mobiledokan.com/mobile/huawei-mate-70-pro-plus",
    ]
    assert sitemap_match_urls(phone("华为", "HUAWEI Mate 70 Pro+"), urls) == [
        "https://www.mobiledokan.com/mobile/huawei-mate-70-pro-plus"
    ]


def test_boolean_value_keeps_unknown_separate_from_no() -> None:
    assert boolean_value("Yes, 50W") is True
    assert boolean_value("No") is False
    assert boolean_value("") is None

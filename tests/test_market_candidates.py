from app.crawlers.market_candidates import candidate_score, normalize_product_url


def test_normalize_product_urls_keeps_only_supported_detail_pages() -> None:
    assert normalize_product_url("jd", "//item.jd.com/1000123.html?bb=1", "https://search.jd.com/") == "https://item.jd.com/1000123.html"
    assert normalize_product_url("tmall", "https://detail.tmall.com/item.htm?id=123&skuId=9", "https://tmall.com/") == "https://detail.tmall.com/item.htm?id=123"
    assert normalize_product_url("pdd", "/goods.html?goods_id=456&page_from=23", "https://mobile.yangkeduo.com/search_result.html") == "https://mobile.yangkeduo.com/goods.html?goods_id=456"
    assert normalize_product_url("jd", "https://example.com/1000123.html", "https://search.jd.com/") is None


def test_candidate_score_rewards_exact_official_variant_and_penalizes_used() -> None:
    exact = candidate_score("Xiaomi 17", "12GB+256GB", "Xiaomi 17 12GB+256GB 小米官方旗舰店")
    used = candidate_score("Xiaomi 17", "12GB+256GB", "Xiaomi 17 12GB+256GB 二手展示机")
    wrong = candidate_score("Xiaomi 17", "12GB+256GB", "Redmi Note 手机壳")
    assert exact > used
    assert exact > wrong
    assert exact >= 20

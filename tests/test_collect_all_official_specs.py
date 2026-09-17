from types import SimpleNamespace

from scripts.collect_all_official_specs import page_matches, screen_type, source_url


def test_screen_type_requires_display_context() -> None:
    assert screen_type("屏幕\n尺寸\n6.7 英寸\n色彩\n10.7 亿色\n类型\nOLED，支持 LTPO") == "OLED"
    assert screen_type("其他手机 OLED\n处理器\n骁龙") is None


def test_page_identity_rejects_redirected_product_index() -> None:
    html = "<title>iPhone - Apple 中国大陆</title><h1>iPhone</h1>"
    assert not page_matches("iPhone 17 Pro", "https://www.apple.com.cn/iphone-17-pro/specs/", "https://www.apple.com.cn/iphone/", html)
    assert page_matches("iPhone 17", "https://www.apple.com.cn/iphone-17/specs/", "https://www.apple.com.cn/iphone-17/specs/", "<title>iPhone 17 - 技术规格</title>")


def test_source_url_rejects_generic_homepage() -> None:
    phone = SimpleNamespace(brand=SimpleNamespace(name="Apple"), model_name="iPhone 17 Pro", official_url="https://www.apple.com.cn/iphone/")
    assert source_url(phone, {}) is None
    assert source_url(phone, {("apple", "iphone 17 pro"): "https://www.apple.com.cn/iphone-17-pro/specs/"}) == "https://www.apple.com.cn/iphone-17-pro/specs/"

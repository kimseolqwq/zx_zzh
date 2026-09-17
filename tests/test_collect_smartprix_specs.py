from types import SimpleNamespace

from scripts.collect_smartprix_specs import (
    extract_fields,
    sitemap_match_urls,
    sitemap_product_urls,
)


def phone(brand: str, model: str) -> SimpleNamespace:
    return SimpleNamespace(brand=SimpleNamespace(name=brand), model_name=model)


def test_smartprix_sitemap_and_matching_handle_hyphenated_model_numbers() -> None:
    xml = """
    <urlset>
      <url><loc>https://www.smartprix.com/mobiles/realme-gt-7-pro-5g-ppd1w3k94nks</loc></url>
      <url><loc>https://www.smartprix.com/mobiles/realme-gt-7-pro-16gb-ram-512gb-ppd1c8evl5xg</loc></url>
    </urlset>
    """
    urls = sitemap_product_urls(xml)
    assert sitemap_match_urls(phone("realme", "真我GT7 Pro"), urls) == [
        "https://www.smartprix.com/mobiles/realme-gt-7-pro-5g-ppd1w3k94nks"
    ]


def test_smartprix_matching_prefers_matching_network_variant() -> None:
    urls = [
        "https://www.smartprix.com/mobiles/vivo-y500-4g-ppd1vf7sqya9",
        "https://www.smartprix.com/mobiles/vivo-y500-5g-ppd1cwci8ij9",
    ]
    assert sitemap_match_urls(phone("vivo", "vivo Y500"), urls)[0].endswith(
        "vivo-y500-5g-ppd1cwci8ij9"
    )
    assert sitemap_match_urls(phone("vivo", "vivo Y500 4G"), urls)[0].endswith(
        "vivo-y500-4g-ppd1vf7sqya9"
    )


def test_smartprix_table_parser_extracts_common_specs() -> None:
    html = """
    <html><body>
      <table>
        <tr><td>Release Date</td><td>July 15, 2025</td></tr>
        <tr><td>Weight</td><td>193 g</td></tr>
        <tr><td>Thickness</td><td>7.76 mm</td></tr>
      </table>
      <table>
        <tr><td>Type</td><td>Color AMOLED Screen</td></tr>
        <tr><td>Size</td><td>6.79 inches, 1200 x 2640 pixels, 120 Hz</td></tr>
      </table>
      <table>
        <tr><td>Rear Camera</td><td>50 MP f/1.9 (Wide Angle)</td></tr>
      </table>
      <table>
        <tr><td>OS</td><td>Android v15</td></tr>
        <tr><td>Chipset</td><td>Qualcomm Snapdragon 6 Gen4</td></tr>
      </table>
      <table>
        <tr><td>IP Rating</td><td>IP68</td></tr>
        <tr><td>Type</td><td>Non-Removable Battery</td></tr>
        <tr><td>Size</td><td>8300 mAh, Li-Po Battery</td></tr>
        <tr><td>Fast Charging</td><td>Yes, 80W Fast Charging</td></tr>
        <tr><td>Wireless Charging</td><td>No</td></tr>
      </table>
    </body></html>
    """
    fields, _ = extract_fields(html, "https://www.smartprix.com/mobiles/test-ppd123456")
    assert fields["release_date"] == "2025-07-15"
    assert fields["cpu"] == "Qualcomm Snapdragon 6 Gen4"
    assert fields["screen_size"] == 6.79
    assert fields["resolution"] == "1200x2640"
    assert fields["refresh_rate"] == 120.0
    assert fields["main_camera_mp"] == 50.0
    assert fields["battery_mah"] == 8300.0
    assert fields["charging_w"] == 80.0
    assert fields["wireless_charging_w"] == 0.0
    assert fields["weight_g"] == 193.0
    assert fields["thickness_mm"] == 7.76
    assert fields["waterproof"] == "IP68"
    assert fields["operating_system"] == "Android v15"


def test_smartprix_parser_falls_back_to_thickness_text() -> None:
    html = "<html><body><p>Thickness: 7.76 mm</p></body></html>"
    fields, _ = extract_fields(html, "https://www.smartprix.com/mobiles/test-ppd123456")
    assert fields["thickness_mm"] == 7.76

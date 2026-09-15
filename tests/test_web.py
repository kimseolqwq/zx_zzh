from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from app.main import app


def test_public_pages_and_health() -> None:
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        library = client.get("/phones")
        assert library.status_code == 200
        assert "按品牌探索在售手机" in library.text
        assert "brand-filter" in library.text
        assert client.get("/phones?q=iPhone&sort=price").status_code == 200
        evaluation = client.get("/evaluation")
        assert evaluation.status_code == 200
        assert "系统评估与证据" in evaluation.text
        assert "核心字段覆盖率" in evaluation.text
        assert "五维加权融合" in evaluation.text
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["database"] == "ok"


def test_admin_login_and_protected_dashboard() -> None:
    with TestClient(app, follow_redirects=True) as client:
        page = client.get("/admin/login")
        token = BeautifulSoup(page.text, "html.parser").select_one("input[name=csrf_token]")["value"]
        response = client.post(
            "/admin/login",
            data={"username": "admin", "password": "Admin@123456", "csrf_token": token},
        )
        assert response.status_code == 200
        assert "DATABASE CONSOLE" in response.text
        assert "商品页实显国补" in response.text
        assert "优惠能否叠加" in response.text


def test_recommendation_result_template(monkeypatch) -> None:
    candidate = {
        "brand": "测试品牌", "model": "测试手机", "variant": "12GB+256GB", "score": 91.2,
        "cpu": "测试处理器", "camera_summary": "5000万像素主摄", "main_camera_mp": 50,
        "screen_size": 6.7, "screen_type": "OLED", "refresh_rate": 120,
        "battery_mah": 5200, "weight_g": 199, "source_url": "https://example.com/specs",
        "reason": "符合预算并且影像配置均衡", "confidence": "高", "model_votes": 3,
        "current_price": 3999.0,
        "image_url": "https://brand.example/phone.png", "image_source_url": "https://brand.example/phone",
        "price_detail": {"store_name": "测试官方旗舰店", "platform": "jd", "platform_name": "京东", "url": "https://item.jd.com/123.html", "purchase_url": "https://item.jd.com/123.html", "link_verified": True, "gov_price": 3499.0, "gov_price_type": "displayed", "billion_subsidy_price": None, "freshness_label": "今日核验", "is_stale": False},
        "platform_prices": [
            {"platform": "jd", "platform_name": "京东", "price": 3999.0, "url": "https://item.jd.com/123.html", "purchase_url": "https://item.jd.com/123.html", "link_verified": True, "is_lowest": True, "gov_price": 3499.0, "gov_price_type": "displayed", "billion_subsidy_price": None, "freshness_label": "今日核验", "is_stale": False, "promotion_labels": ["限时直降"]},
            {"platform": "tmall", "platform_name": "天猫", "price": 4099.0, "url": "https://detail.tmall.com/item.htm?id=1", "purchase_url": "https://detail.tmall.com/item.htm?id=1", "link_verified": True, "is_lowest": False, "gov_price": None, "billion_subsidy_price": None, "freshness_label": "2天前核验", "is_stale": False, "promotion_labels": []},
            {"platform": "pdd", "platform_name": "拼多多", "price": None, "url": None, "purchase_url": "https://mobile.yangkeduo.com/search_result.html?search_key=test", "link_verified": False, "is_lowest": False, "freshness_label": "尚未核验", "is_stale": True, "promotion_labels": []},
        ],
        "components": {"requirement_match": 92, "model_consensus": 90, "data_trust": 95, "value": 88, "freshness": 96},
        "score_breakdown": [
            {"label": "需求匹配", "raw": 92, "weight": 35, "contribution": 32.2},
            {"label": "模型共识", "raw": 90, "weight": 25, "contribution": 22.5},
        ],
        "evidence_lines": ["公开参考价 ¥3,999，比预算低 ¥1,001。", "3 个模型有效评分。"],
    }
    fake_result = {
        "run_id": 99, "total_latency_ms": 1234.0, "results": [candidate],
        "models": [{"name": "qwen3:8b", "success": True, "json_parse_success": True, "latency_ms": 500, "tokens_per_second": 50}],
        "formula": "测试融合公式",
    }
    monkeypatch.setattr("app.routers.web.recommend", lambda _db, _requirements: fake_result)
    with TestClient(app) as client:
        response = client.post(
            "/recommend",
            data={"budget": "5000", "usage": "摄影创作", "brand": "", "storage": "256", "details": "旅行拍照"},
        )
        assert response.status_code == 200
        assert "测试手机" in response.text
        assert "测试融合公式" in response.text
        assert "天猫" in response.text
        assert "https://item.jd.com/123.html" in response.text
        assert "https://brand.example/phone.png" in response.text
        assert "data-recommendation-stack" in response.text
        assert "data-recommendation-card" in response.text
        assert "搜索页" in response.text
        assert "实显国补" in response.text
        assert "今日核验" in response.text
        assert "Top 1 结论证据链" in response.text
        assert "贡献分" in response.text
        assert "https://mobile.yangkeduo.com/search_result.html?search_key=test" in response.text

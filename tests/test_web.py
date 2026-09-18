from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from urllib.parse import urlparse

from app.database import SessionLocal
from app.main import app
from app.models import PhoneDislike, PhoneLike, PhoneModel, PhoneVariant, User


def test_public_pages_and_health() -> None:
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert "懂手机" in client.get("/").text
        assert "也比你，更懂你" in client.get("/").text
        library = client.get("/phones")
        assert library.status_code == 200
        assert "按品牌探索在售手机" in library.text
        assert "brand-filter" in library.text
        assert "data-like-phone" in library.text
        assert "data-dislike-phone" in library.text
        assert client.get("/phones?q=iPhone&sort=price").status_code == 200
        empty_brand = client.get("/phones?q=iphone+duo&brand=&sort=newest")
        assert empty_brand.status_code == 200
        assert "int_parsing" not in empty_brand.text
        assert "iPhone Duo" in empty_brand.text
        evaluation = client.get("/evaluation")
        assert evaluation.status_code == 200
        assert "系统评估与证据" in evaluation.text
        assert "核心字段覆盖率" in evaluation.text
        assert "五维加权融合" in evaluation.text
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["database"] == "ok"


def test_phone_detail_has_version_specific_platform_search_links() -> None:
    with SessionLocal() as db:
        phone_id = db.scalar(
            select(PhoneModel.id)
            .join(PhoneModel.variants)
            .where(PhoneModel.is_active.is_(True), PhoneVariant.is_active.is_(True))
            .order_by(PhoneModel.id)
            .limit(1)
        )
    with TestClient(app) as client:
        response = client.get(f"/phones/{phone_id}")
    assert response.status_code == 200
    soup = BeautifulSoup(response.text, "html.parser")
    links = {urlparse(a["href"]).netloc for a in soup.select(".variant-search-links a")}
    assert {"search.jd.com", "list.tmall.com", "mobile.yangkeduo.com"} <= links
    assert "data-like-phone" in response.text
    assert "data-dislike-phone" in response.text


def test_phone_like_endpoint_is_idempotent() -> None:
    visitor_id = "pytest-like-visitor-2026"
    with SessionLocal() as db:
        phone_id = db.scalar(
            select(PhoneModel.id)
            .where(PhoneModel.is_active.is_(True))
            .order_by(PhoneModel.id)
            .limit(1)
        )
        assert phone_id is not None
        db.execute(
            delete(PhoneLike).where(
                PhoneLike.phone_id == phone_id,
                PhoneLike.visitor_id == visitor_id,
            )
        )
        db.commit()
        baseline = db.scalar(
            select(func.count(PhoneLike.id)).where(PhoneLike.phone_id == phone_id)
        ) or 0
    try:
        with TestClient(app) as client:
            first = client.post(
                f"/api/phones/{phone_id}/like",
                json={"visitor_id": visitor_id, "liked": True},
            )
            assert first.status_code == 200
            assert first.json()["liked"] is True
            assert first.json()["like_count"] == baseline + 1

            repeated = client.post(
                f"/api/phones/{phone_id}/like",
                json={"visitor_id": visitor_id, "liked": True},
            )
            assert repeated.json()["like_count"] == baseline + 1

            removed = client.post(
                f"/api/phones/{phone_id}/like",
                json={"visitor_id": visitor_id, "liked": False},
            )
            assert removed.json()["liked"] is False
            assert removed.json()["like_count"] == baseline
    finally:
        with SessionLocal() as db:
            db.execute(
                delete(PhoneLike).where(
                    PhoneLike.phone_id == phone_id,
                    PhoneLike.visitor_id == visitor_id,
                )
            )
            db.commit()


def test_phone_dislike_switches_from_like_and_is_idempotent() -> None:
    visitor_id = "pytest-dislike-visitor-2026"
    with SessionLocal() as db:
        phone_id = db.scalar(
            select(PhoneModel.id)
            .where(PhoneModel.is_active.is_(True))
            .order_by(PhoneModel.id)
            .limit(1)
        )
        assert phone_id is not None
        db.execute(
            delete(PhoneLike).where(
                PhoneLike.phone_id == phone_id,
                PhoneLike.visitor_id == visitor_id,
            )
        )
        db.execute(
            delete(PhoneDislike).where(
                PhoneDislike.phone_id == phone_id,
                PhoneDislike.visitor_id == visitor_id,
            )
        )
        db.commit()
        base_likes = db.scalar(
            select(func.count(PhoneLike.id)).where(PhoneLike.phone_id == phone_id)
        ) or 0
        base_dislikes = db.scalar(
            select(func.count(PhoneDislike.id)).where(PhoneDislike.phone_id == phone_id)
        ) or 0
    try:
        with TestClient(app) as client:
            liked = client.post(
                f"/api/phones/{phone_id}/like",
                json={"visitor_id": visitor_id, "liked": True},
            )
            assert liked.json()["like_count"] == base_likes + 1
            assert liked.json()["dislike_count"] == base_dislikes

            disliked = client.post(
                f"/api/phones/{phone_id}/dislike",
                json={"visitor_id": visitor_id, "disliked": True},
            )
            assert disliked.json()["liked"] is False
            assert disliked.json()["disliked"] is True
            assert disliked.json()["like_count"] == base_likes
            assert disliked.json()["dislike_count"] == base_dislikes + 1

            repeated = client.post(
                f"/api/phones/{phone_id}/dislike",
                json={"visitor_id": visitor_id, "disliked": True},
            )
            assert repeated.json()["like_count"] == base_likes
            assert repeated.json()["dislike_count"] == base_dislikes + 1

            removed = client.post(
                f"/api/phones/{phone_id}/dislike",
                json={"visitor_id": visitor_id, "disliked": False},
            )
            assert removed.json()["disliked"] is False
            assert removed.json()["like_count"] == base_likes
            assert removed.json()["dislike_count"] == base_dislikes
    finally:
        with SessionLocal() as db:
            db.execute(
                delete(PhoneLike).where(
                    PhoneLike.phone_id == phone_id,
                    PhoneLike.visitor_id == visitor_id,
                )
            )
            db.execute(
                delete(PhoneDislike).where(
                    PhoneDislike.phone_id == phone_id,
                    PhoneDislike.visitor_id == visitor_id,
                )
            )
            db.commit()


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
        assert "公开活动价" in response.text
        assert "国补" not in response.text
        assert "百亿补贴" not in response.text
        assert "默认管理员密码" in response.text
        search = client.get("/admin?q=iPhone+Duo")
        assert search.status_code == 200
        assert "iPhone Duo" in search.text
        assert 'name="q"' in search.text
        assert "iPhone 17" not in search.text
        with SessionLocal() as db:
            admin_id = db.scalar(select(User.id).where(User.username == "admin"))
        logs = client.get(f"/admin/audit-logs?admin_id={admin_id}")
        assert logs.status_code == 200
        assert "操作日志" in logs.text
        assert "全部管理员" in logs.text
        assert "登录" in logs.text


def test_recommendation_result_template(monkeypatch) -> None:
    candidate = {
        "brand": "测试品牌", "model": "测试手机", "variant": "12GB+256GB", "score": 91.2,
        "model_id": 123, "like_count": 7, "dislike_count": 2,
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
            {"label": "需求匹配", "raw": 92, "weight": 40, "contribution": 36.8},
            {"label": "模型共识", "raw": 90, "weight": 15, "contribution": 13.5},
        ],
        "evidence_lines": ["公开参考价 ¥3,999，比预算低 ¥1,001。", "3 个模型有效评分。"],
    }
    fake_result = {
        "run_id": 99, "total_latency_ms": 1234.0, "results": [candidate],
        "models": [{"name": "qwen3:8b", "success": True, "json_parse_success": True, "latency_ms": 500, "tokens_per_second": 50}],
        "formula": "测试融合公式",
        "score_weights": [
            {"key": "requirement_match", "label": "需求匹配", "weight": 40},
            {"key": "model_consensus", "label": "模型共识", "weight": 15},
            {"key": "data_trust", "label": "数据可信", "weight": 20},
            {"key": "value", "label": "预算利用", "weight": 20},
            {"key": "freshness", "label": "时效性", "weight": 5},
        ],
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
        assert 'data-like-phone="123"' in response.text
        assert 'data-dislike-phone="123"' in response.text
        assert "去平台搜索" in response.text
        assert "国补" not in response.text
        assert "百亿补贴" not in response.text
        assert "今日核验" in response.text
        assert "Top 1 结论证据链" in response.text
        assert "贡献分" in response.text
        assert "https://mobile.yangkeduo.com/search_result.html?search_key=test" in response.text

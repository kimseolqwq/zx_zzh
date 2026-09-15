from pathlib import Path
import time

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import ModelRun
from app.seed import seed_demo_data
from app.services.ollama import ModelOpinion
from app.services.recommendation import (
    Requirements,
    _model_scores,
    _parallel_model_opinions,
    _usage_score,
    load_candidates,
    recommend,
)


class FakeOllama:
    def installed_models(self):
        return ["qwen3:1.7b", "qwen2.5:1.5b", "gemma3:1b"]

    def chat_json(self, model_name, _system, _prompt):
        rankings = [
            {"variant_id": 1, "score": 90, "reason": "影像和预算较均衡", "pros": ["影像"], "cons": ["较重"]},
            {"variant_id": 2, "score": 88, "reason": "性能和价格突出", "pros": ["性能"], "cons": []},
            {"variant_id": 3, "score": 82, "reason": "视频体验稳定", "pros": ["视频"], "cons": ["价格"]},
            {"variant_id": 4, "score": 91, "reason": "长焦适合旅行", "pros": ["长焦"], "cons": ["重量"]},
        ]
        return ModelOpinion(model_name, True, 100, 120, 80, 55.0, "{}", {"rankings": rankings})


def test_model_scores_accepts_unambiguous_nested_score_value() -> None:
    opinion = ModelOpinion(
        "qwen2.5:1.5b", True, 100, 10, 10, 20.0, "",
        {"scores": {"101": {"scores": 85}, "102": {"score": 72}}},
    )
    assert _model_scores([opinion]) == {
        101: [(85.0, {"variant_id": "101", "score": {"scores": 85}})],
        102: [(72.0, {"variant_id": "102", "score": {"score": 72}})],
    }


def test_model_scores_accepts_plural_list_score_and_dimension_average() -> None:
    plural = ModelOpinion(
        "gemma", True, 10, 1, 1, 10, "{}",
        {"scores": [{"id": 101, "scores": 88}]},
    )
    dimensions = ModelOpinion(
        "qwen", True, 10, 1, 1, 10, "{}",
        {"scores": {"102": {"performance": 90, "camera": 70, "battery": 80}}},
    )
    parsed = _model_scores([plural, dimensions])
    assert parsed[101][0][0] == 88
    assert parsed[102][0][0] == 80


def test_recommendation_fuses_three_models(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'test.db').as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as db:
        seed_demo_data(db)
        monkeypatch.setattr("app.services.recommendation.OllamaClient", FakeOllama)
        result = recommend(db, Requirements(5000, "摄影创作", None, 256, "旅行拍照"))
        assert len(result["results"]) == 3
        assert len({item["model_id"] for item in result["results"]}) == 3
        assert all(item["model_votes"] == 3 for item in result["results"])
        assert all("推荐" in item["reason"] and "预算" in item["reason"] for item in result["results"])
        assert all(len(item["platform_prices"]) == 3 for item in result["results"])
        assert {quote["platform"] for quote in result["results"][0]["platform_prices"]} == {"jd", "tmall", "pdd"}
        assert all(quote["purchase_url"].startswith("https://") for quote in result["results"][0]["platform_prices"])
        assert any(not quote["link_verified"] for quote in result["results"][0]["platform_prices"])
        assert result["results"][0]["score"] >= result["results"][1]["score"]
        assert all("score_breakdown" in item and "evidence_lines" in item for item in result["results"])
        assert db.scalar(select(func.count()).select_from(ModelRun)) == 3


def test_candidate_filter_treats_budget_and_brand_as_hard_constraints(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'strict.db').as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as db:
        seed_demo_data(db)
        candidates = load_candidates(db, Requirements(5000, "综合体验", "Apple", 256, ""))
        assert all(item["brand"] == "Apple" for item in candidates)
        assert all(item["current_price"] <= 5000 for item in candidates)
        assert load_candidates(db, Requirements(5000, "综合体验", "不存在品牌", 256, "")) == []
        assert load_candidates(db, Requirements(500, "综合体验", None, 1024, "")) == []


def test_domain_profiles_reward_relevant_hardware_instead_of_low_price_only():
    base = {
        "model": "普通手机", "cpu": None, "refresh_rate": 60, "main_camera_mp": 50,
        "battery_mah": 4500, "charging_w": 20, "weight_g": 220,
        "ram_gb": 8, "storage_gb": 256,
    }
    gaming = {**base, "model": "iQOO 15", "cpu": "第五代骁龙8至尊版", "refresh_rate": 165, "battery_mah": 7000, "charging_w": 100, "ram_gb": 16}
    camera = {**base, "model": "HUAWEI Pura 90 Ultra", "main_camera_mp": 200, "storage_gb": 512}
    assert _usage_score(gaming, "重度游戏") > _usage_score(base, "重度游戏") + 20
    assert _usage_score(camera, "摄影创作") > _usage_score(base, "摄影创作") + 15


def test_three_model_calls_are_parallel():
    class SlowClient:
        def chat_json(self, model_name, _system, _prompt):
            time.sleep(0.1)
            return ModelOpinion(model_name, True, 100, 10, 10, 50, "{}", {"scores": []})

    started = time.perf_counter()
    opinions = _parallel_model_opinions(
        SlowClient(), {"qwen3:1.7b", "qwen2.5:1.5b", "gemma3:1b"}, "system", "prompt"
    )
    elapsed = time.perf_counter() - started
    assert len(opinions) == 3
    assert elapsed < 0.22


def test_model_scores_accepts_small_model_json_variants_and_deduplicates_votes():
    opinion = ModelOpinion(
        "gemma3:1b", True, 100, 10, 10, 50, "{}",
        {"scores": [{"id": 7, "score": 88}, {"id": 7, "score": 99}, [8, 77]]},
    )
    scores = _model_scores([opinion])
    assert scores[7][0][0] == 88
    assert len(scores[7]) == 1
    assert scores[8][0][0] == 77
    object_scores = _model_scores(
        [ModelOpinion("qwen", True, 10, 1, 1, 10, "{}", {"scores": {"7": 91, "8": 79}})]
    )
    assert object_scores[7][0][0] == 91
    assert object_scores[8][0][0] == 79

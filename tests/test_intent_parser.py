from app.services.intent_parser import parse_intent
from app.services.ollama import ModelOpinion


def test_rule_intent_extracts_budget_priorities_and_negation() -> None:
    intent = parse_intent(
        "预算6000到8000，拍照第一，性能第二，不要OPPO，越贵越好，最好轻一点",
        "综合体验",
        ["OPPO", "小米"],
    )
    assert intent["min_budget"] == 6000
    assert intent["max_budget"] == 8000
    assert intent["price_preference"] == "high"
    assert intent["avoid_brands"] == ["OPPO"]
    assert "lightweight" in intent["soft_requirements"]
    assert intent["priority_weights"]["camera"] > intent["priority_weights"]["performance"]


def test_rule_intent_handles_low_price_and_foldable_negation() -> None:
    low = parse_intent("不要太贵，越便宜越好", "综合体验", [])
    assert low["price_preference"] == "low"
    slab = parse_intent("不要折叠屏，直板机", "综合体验", [])
    assert slab["form_factor"] == "slab"


def test_llm_intent_is_merged_when_available() -> None:
    class FakeClient:
        def chat_json(self, model_name, _system, _prompt):
            return ModelOpinion(
                model_name,
                True,
                10,
                1,
                1,
                10,
                "{}",
                {
                    "price_preference": "high",
                    "must_have": ["waterproof"],
                    "priority_weights": {"camera": 0.7, "battery": 0.2, "performance": 0.1},
                    "reason": "影像优先并需要防水",
                },
            )

    intent = parse_intent("拍照最重要，续航第二，需要防水，越贵越好", "摄影创作", [], FakeClient())
    assert intent["source"] == "rules+llm"
    assert intent["price_preference"] == "high"
    assert "waterproof" in intent["must_have"]
    assert intent["priority_weights"]["camera"] > 0.5


def test_feature_mentions_are_not_all_treated_as_hard_requirements() -> None:
    preferred = parse_intent("最好有防水，拍照优先", "摄影创作", [])
    assert "waterproof" in preferred["soft_requirements"]
    assert "waterproof" not in preferred["must_have"]

    ignored = parse_intent("防水不重要，随便有没有", "综合体验", [])
    assert "waterproof" not in ignored["soft_requirements"]
    assert "waterproof" not in ignored["must_have"]

    required = parse_intent("必须5G，也需要NFC", "综合体验", [])
    assert "five_g" in required["must_have"]
    assert "nfc" in required["must_have"]

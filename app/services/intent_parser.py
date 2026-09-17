from __future__ import annotations

import json
import re
from typing import Any

from app.config import settings


WEIGHT_KEYS = ("camera", "performance", "display", "battery", "portability")
USAGE_WEIGHTS = {
    "摄影创作": {"camera": 0.60, "performance": 0.13, "display": 0.11, "battery": 0.10, "portability": 0.06},
    "重度游戏": {"camera": 0.04, "performance": 0.40, "display": 0.25, "battery": 0.23, "portability": 0.08},
    "轻薄续航": {"camera": 0.05, "performance": 0.16, "display": 0.10, "battery": 0.35, "portability": 0.34},
    "综合体验": {"camera": 0.24, "performance": 0.24, "display": 0.20, "battery": 0.20, "portability": 0.12},
}
FEATURE_TOKENS = {
    "camera": ("拍照", "摄影", "影像", "相机", "长焦", "潜望", "人像", "视频"),
    "performance": ("性能", "游戏", "芯片", "处理器", "帧率", "电竞", "跑分"),
    "display": ("屏幕", "显示", "高刷", "刷新率", "护眼", "分辨率", "亮度"),
    "battery": ("续航", "电池", "充电", "快充", "省电"),
    "portability": ("轻薄", "重量", "手感", "小屏", "便携", "轻一点"),
}
FEATURE_TERMS = {
    "wireless_charging": ("无线充", "无线充电"),
    "waterproof": ("防水",),
    "telephoto": ("长焦", "潜望", "望远"),
    "small_screen": ("小屏", "小尺寸", "屏幕小"),
    "lightweight": ("轻薄", "轻一点", "轻一些", "重量轻", "手感轻"),
    "large_battery": ("大电池", "续航第一", "续航优先", "电池大", "续航强"),
    "high_refresh": ("高刷", "高刷新率", "144hz", "165hz"),
    "nfc": ("nfc",),
    "five_g": ("5g",),
}
HARD_REQUIREMENT_TRIGGERS = {
    "wireless_charging": ("需要无线充", "要无线充", "必须无线充", "一定要无线充", "无线充是刚需", "需要无线充电", "必须无线充电"),
    "waterproof": ("需要防水", "要防水", "必须防水", "一定要防水", "防水是刚需"),
    "telephoto": ("需要长焦", "要长焦", "要有长焦", "必须长焦", "一定要长焦", "必须有长焦"),
    "small_screen": ("必须小屏", "一定要小屏", "需要小屏"),
    "lightweight": ("必须轻薄", "一定要轻薄", "必须轻一点"),
    "large_battery": ("必须大电池", "一定要大电池", "续航必须是第一", "续航必须第一"),
    "high_refresh": ("必须高刷", "一定要高刷", "需要高刷新率"),
    "nfc": ("需要nfc", "要nfc", "必须有nfc", "必须nfc", "一定要nfc"),
    "five_g": ("需要5g", "要5g", "必须5g", "必须有5g", "一定要5g"),
}
NEGATIVE_REQUIREMENT_TOKENS = (
    "不需要", "不用", "无需", "不要", "不想要", "无所谓", "不重要",
    "可有可无", "没有要求", "没要求", "非必须", "不强制",
)


def _normalise_weights(weights: dict[str, float]) -> dict[str, float]:
    clean = {key: max(0.0, float(weights.get(key, 0))) for key in WEIGHT_KEYS}
    total = sum(clean.values())
    if total <= 0:
        return dict(USAGE_WEIGHTS["综合体验"])
    return {key: round(value / total, 4) for key, value in clean.items()}


def _rule_intent(text: str, usage: str, known_brands: list[str]) -> dict[str, Any]:
    text = re.sub(r"\s+", "", text or "")
    result: dict[str, Any] = {
        "min_budget": None,
        "max_budget": None,
        "price_preference": "balanced",
        "form_factor": "any",
        "preferred_brands": [],
        "avoid_brands": [],
        "must_have": [],
        "soft_requirements": [],
        "avoid": [],
        "priority_weights": dict(USAGE_WEIGHTS.get(usage, USAGE_WEIGHTS["综合体验"])),
        "confidence": "low" if text else "none",
        "source": "rules",
    }

    range_match = re.search(r"(\d{3,5})(?:元)?(?:-|~|到|至)(\d{3,5})", text)
    if range_match:
        low, high = int(range_match.group(1)), int(range_match.group(2))
        result["min_budget"], result["max_budget"] = min(low, high), max(low, high)
    else:
        around = re.search(r"(?:预算|价格)?(\d{3,5})(?:元)?(?:左右|上下)", text)
        if around:
            value = int(around.group(1))
            result["min_budget"], result["max_budget"] = int(value * 0.9), int(value * 1.1)
        else:
            max_match = re.search(r"(?:不超过|最多|最高|预算|价格|封顶)(\d{3,5})(?:元)?(?:以内)?", text)
            if max_match:
                result["max_budget"] = int(max_match.group(1))
            min_match = re.search(r"(?:至少|最低)(\d{3,5})(?:元)?", text)
            if min_match:
                result["min_budget"] = int(min_match.group(1))

    if any(token in text for token in ("越贵越好", "尽量贵", "上顶配", "用满预算", "接近预算上限", "价格越高越好")):
        result["price_preference"] = "high"
    elif any(token in text for token in ("越便宜越好", "低价优先", "省钱", "价格越低越好", "性价比优先", "不要太贵", "尽量便宜", "便宜点", "便宜些")):
        result["price_preference"] = "low"

    if any(token in text for token in ("不要折叠", "不需要折叠", "直板机", "不要折叠屏")):
        result["form_factor"] = "slab"
    elif any(token in text for token in ("折叠屏", "折叠手机", "foldable", "flip")):
        result["form_factor"] = "foldable"

    text_cf = text.casefold()
    for key, terms in FEATURE_TERMS.items():
        term = next((item for item in terms if item.casefold() in text_cf), None)
        if term is None:
            continue
        if any(
            f"{negative}{term}".casefold() in text_cf
            or f"{term}{negative}".casefold() in text_cf
            for negative in NEGATIVE_REQUIREMENT_TOKENS
        ):
            continue
        if any(trigger.casefold() in text_cf for trigger in HARD_REQUIREMENT_TRIGGERS[key]):
            result["must_have"].append(key)
        else:
            result["soft_requirements"].append(key)

    if any(token in text for token in ("不要曲面", "不要曲面屏")):
        result["avoid"].append("curved_screen")
    if any(token in text for token in ("不要太重", "不要重", "别太重", "越轻越好")):
        result["avoid"].append("heavy")

    for brand in known_brands:
        compact = re.sub(r"\s+", "", brand).casefold()
        if not compact or compact not in text.casefold():
            continue
        if any(token in text for token in (f"不要{brand}", f"除了{brand}", f"排除{brand}", f"不想要{brand}")):
            result["avoid_brands"].append(brand)
        if any(token in text for token in (f"只要{brand}", f"优先{brand}", f"必须{brand}")):
            result["preferred_brands"].append(brand)

    weights = dict(result["priority_weights"])
    explicit_priority = False
    for feature, tokens in FEATURE_TOKENS.items():
        for token in tokens:
            if token.casefold() in text.casefold():
                weights[feature] = weights.get(feature, 0) + 0.18
                explicit_priority = True
    for feature, tokens in FEATURE_TOKENS.items():
        if any(re.search(rf"(?:{re.escape(token)})[^。；，,]{{0,8}}(?:最重要|优先|第一)", text, re.IGNORECASE) for token in tokens):
            weights[feature] = weights.get(feature, 0) + 0.35
            explicit_priority = True
        elif any(re.search(rf"(?:{re.escape(token)})[^。；，,]{{0,8}}第二", text, re.IGNORECASE) for token in tokens):
            weights[feature] = weights.get(feature, 0) + 0.18
            explicit_priority = True
    for first, second in re.findall(r"([\u4e00-\u9fff]{2,8})比([\u4e00-\u9fff]{2,8})重要", text):
        for feature, tokens in FEATURE_TOKENS.items():
            if any(token in first for token in tokens):
                weights[feature] = weights.get(feature, 0) + 0.28
            if any(token in second for token in tokens):
                weights[feature] = max(0, weights.get(feature, 0) - 0.12)
        explicit_priority = True
    result["priority_weights"] = _normalise_weights(weights)
    result["confidence"] = "high" if explicit_priority or range_match or result["form_factor"] != "any" else "medium" if text else "none"
    return result


def _llm_prompt(text: str, usage: str, known_brands: list[str]) -> tuple[str, str]:
    system = (
        "你是手机推荐系统的需求解析器。把用户中文自然语言转换成JSON。"
        "不得猜预算或品牌；不确定的字段使用null、空数组或balanced。只输出JSON。"
    )
    user = f"""
用户用途：{usage}
已知品牌：{json.dumps(known_brands, ensure_ascii=False)}
用户原话：{text}
输出字段：
{{
  "min_budget": null或数字,
  "max_budget": null或数字,
  "price_preference": "high|low|balanced",
  "form_factor": "foldable|slab|any",
  "preferred_brands": [],
  "avoid_brands": [],
  "must_have": ["wireless_charging","waterproof","telephoto","small_screen","lightweight","large_battery","high_refresh","nfc","five_g"],
  "soft_requirements": ["wireless_charging","waterproof","telephoto","small_screen","lightweight","large_battery","high_refresh","nfc","five_g"],
  "avoid": ["curved_screen","heavy"],
  "priority_weights": {{"camera":0-1,"performance":0-1,"display":0-1,"battery":0-1,"portability":0-1}},
  "reason": "一句话说明主要偏好"
}}
""".strip()
    return system, user


def _llm_intent(client: Any, text: str, usage: str, known_brands: list[str]) -> dict[str, Any] | None:
    model_names = list(settings.ollama_models or ("qwen3:1.7b", "qwen2.5:1.5b", "gemma3:1b"))
    model = next((name for name in model_names if "qwen" in name.casefold()), model_names[0])
    system, user = _llm_prompt(text, usage, known_brands)
    opinion = client.chat_json(model, system, user)
    if not opinion.success or not isinstance(opinion.parsed, dict):
        return None
    return opinion.parsed


def _merge(rule: dict[str, Any], llm: dict[str, Any] | None) -> dict[str, Any]:
    if not llm:
        return rule
    merged = dict(rule)
    # Hard constraints are intentionally rule-only.  Small models sometimes
    # invent brand exclusions or mandatory features that the user never said.
    text = re.sub(r"\s+", "", str(llm.get("_source_text") or ""))
    supported_must = []
    llm_must = llm.get("must_have")
    if isinstance(llm_must, list):
        for item in llm_must:
            key = str(item)
            triggers = HARD_REQUIREMENT_TRIGGERS.get(key, ())
            if triggers and any(trigger.casefold() in text.casefold() for trigger in triggers):
                supported_must.append(key)
    merged["must_have"] = list(dict.fromkeys([*merged.get("must_have", []), *supported_must]))
    llm_soft = llm.get("soft_requirements")
    supported_soft = []
    if isinstance(llm_soft, list):
        for item in llm_soft:
            key = str(item)
            triggers = FEATURE_TERMS.get(key, ())
            if triggers and any(trigger.casefold() in text.casefold() for trigger in triggers):
                supported_soft.append(key)
    merged["soft_requirements"] = [
        item for item in dict.fromkeys([*merged.get("soft_requirements", []), *supported_soft])
        if item not in merged["must_have"]
    ]
    llm_weights = llm.get("priority_weights")
    if isinstance(llm_weights, dict):
        combined = {}
        for key in WEIGHT_KEYS:
            rule_value = float(rule["priority_weights"].get(key, 0))
            llm_value = max(0.0, min(1.0, float(llm_weights.get(key, 0) or 0)))
            combined[key] = 0.35 * rule_value + 0.65 * llm_value if llm_value else rule_value
        merged["priority_weights"] = _normalise_weights(combined)
    merged["source"] = "rules+llm"
    merged["llm_reason"] = str(llm.get("reason") or "")[:160]
    return merged


def parse_intent(text: str, usage: str, known_brands: list[str], client: Any | None = None) -> dict[str, Any]:
    rule = _rule_intent(text, usage, known_brands)
    if not (text or "").strip() or client is None:
        return rule
    try:
        llm = _llm_intent(client, text, usage, known_brands)
        if llm is not None:
            llm["_source_text"] = text
        return _merge(rule, llm)
    except Exception:
        return rule

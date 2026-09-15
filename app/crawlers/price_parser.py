from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class ParsedPrice:
    regular_price: float | None = None
    public_sale_price: float | None = None
    displayed_gov_price: float | None = None
    billion_subsidy_price: float | None = None
    confidence: str = "low"
    evidence: list[str] | None = None


def _prices_near(label: str, text: str) -> list[tuple[float, str]]:
    pattern = rf"[^\n]{{0,35}}{label}[^\n]{{0,45}}"
    values: list[tuple[float, str]] = []
    for match in re.finditer(pattern, text, flags=re.IGNORECASE):
        evidence = match.group(0).strip()
        for price_match in re.finditer(r"(?:¥|￥)?\s*(\d{3,5}(?:\.\d{1,2})?)\s*元?", evidence):
            value = float(price_match.group(1))
            if 300 <= value <= 30000:
                values.append((value, evidence))
    return values


def parse_price_text(text: str) -> ParsedPrice:
    """只接受带明确标签的价格；歧义时降级为待人工复核。"""
    gov = _prices_near(r"国补(?:价|后|到手)?", text)
    subsidy = _prices_near(r"百亿补贴(?:价|后|到手)?", text)
    sale = _prices_near(r"(?:活动价|到手价|优惠价|券后价)", text)
    regular = _prices_near(r"(?:售价|京东价|商城价|官方价)", text)
    evidence = [item[1] for group in [gov, subsidy, sale, regular] for item in group][:12]
    result = ParsedPrice(
        regular_price=min((item[0] for item in regular), default=None),
        public_sale_price=min((item[0] for item in sale), default=None),
        displayed_gov_price=min((item[0] for item in gov), default=None),
        billion_subsidy_price=min((item[0] for item in subsidy), default=None),
        evidence=evidence,
    )
    found_groups = sum(value is not None for value in [result.regular_price, result.public_sale_price, result.displayed_gov_price, result.billion_subsidy_price])
    result.confidence = "high" if found_groups >= 2 and len(evidence) <= 6 else "medium" if found_groups else "low"
    return result

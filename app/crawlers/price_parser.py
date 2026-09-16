from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class ParsedPrice:
    regular_price: float | None = None
    public_sale_price: float | None = None
    confidence: str = "low"
    evidence: list[str] | None = None


def _prices_near(label: str, text: str) -> list[tuple[float, str]]:
    pattern = rf"[^\n]{{0,35}}{label}[^\n]{{0,45}}"
    values: list[tuple[float, str]] = []
    for match in re.finditer(pattern, text, flags=re.IGNORECASE):
        evidence = match.group(0).strip()
        if "补贴" in evidence:
            continue
        for price_match in re.finditer(r"(?:¥|￥)?\s*(\d{3,5}(?:\.\d{1,2})?)\s*元?", evidence):
            value = float(price_match.group(1))
            if 300 <= value <= 30000:
                values.append((value, evidence))
    return values


def parse_price_text(text: str) -> ParsedPrice:
    """只接受带明确标签的价格；歧义时降级为待人工复核。"""
    text = "\n".join(line for line in text.splitlines() if "补贴" not in line and "国补" not in line)
    sale = _prices_near(r"(?:活动价|到手价|优惠价|券后价)", text)
    regular = _prices_near(r"(?:售价|京东价|商城价|官方价|优惠前)", text)
    # Tmall commonly renders the label, currency sign and price on separate
    # lines, followed by an explicit pre-discount price.  The two labels make
    # this relation unambiguous enough to parse without guessing.
    for match in re.finditer(
        r"券后\s*\n\s*[¥￥]\s*\n?\s*(\d{3,5}(?:\.\d{1,2})?)\s*\n\s*优惠前\s*[¥￥]\s*(\d{3,5}(?:\.\d{1,2})?)",
        text,
        flags=re.IGNORECASE,
    ):
        sale.append((float(match.group(1)), match.group(0).strip()))
        regular.append((float(match.group(2)), match.group(0).strip()))
    evidence = [item[1] for group in [sale, regular] for item in group][:12]
    result = ParsedPrice(
        regular_price=min((item[0] for item in regular), default=None),
        public_sale_price=min((item[0] for item in sale), default=None),
        evidence=evidence,
    )
    found_groups = sum(value is not None for value in [result.regular_price, result.public_sale_price])
    result.confidence = "high" if found_groups >= 2 and len(evidence) <= 6 else "medium" if found_groups else "low"
    return result

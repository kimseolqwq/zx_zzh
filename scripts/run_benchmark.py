from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings
from app.database import SessionLocal
from app.services.ollama import ModelOpinion, OllamaClient
from app.services.recommendation import (
    Requirements, SCORE_WEIGHTS, _base_components, _build_prompt, _model_scores, load_candidates,
)

MODES = ("rule_only", "single_qwen", "three_model_fusion", "no_consensus")


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def rank_candidates(candidates: list[dict], requirements: Requirements, opinions: list[ModelOpinion], mode: str) -> list[dict]:
    prices = [item["current_price"] for item in candidates if item.get("current_price") is not None]
    selected = opinions
    if mode == "single_qwen":
        selected = [item for item in opinions if item.model_name.startswith("qwen")]
    scores_by_id = _model_scores(selected)
    ranked = []
    for candidate in candidates:
        components = _base_components(candidate, requirements, prices)
        values = [entry[0] for entry in scores_by_id.get(candidate["variant_id"], [])]
        if mode == "rule_only":
            score = (components["requirement_match"] * .55 + components["data_trust"] * .25
                     + components["value"] * .15 + components["freshness"] * .05)
        elif mode == "no_consensus":
            score = sum(components[key] * weight for key, (_, weight) in SCORE_WEIGHTS.items() if key != "model_consensus") / .75
        else:
            if values:
                deviation = statistics.pstdev(values) if len(values) > 1 else 0
                consensus = statistics.mean(values) * max(.7, 1 - deviation / 100)
            else:
                consensus = components["requirement_match"] * .7
            components["model_consensus"] = consensus
            score = sum(components[key] * weight for key, (_, weight) in SCORE_WEIGHTS.items())
        ranked.append({"variant_id": candidate["variant_id"], "brand": candidate["brand"], "model": candidate["model"],
                       "variant": candidate["variant"], "price": candidate["current_price"], "score": round(score, 2),
                       "model_votes": len(values)})
    return sorted(ranked, key=lambda item: item["score"], reverse=True)[:5]


def compliance(result: dict | None, case: dict) -> bool:
    if result is None:
        return not case["expect_results"]
    return (result["price"] is not None and result["price"] <= case["budget"]
            and (not case.get("brand") or case["brand"].casefold() in result["brand"].casefold()))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the fixed 30-query recommendation benchmark and ablation study")
    parser.add_argument("--cases", type=Path, default=PROJECT_ROOT / "config" / "evaluation_cases.json")
    parser.add_argument("--rules-only", action="store_true", help="Skip Ollama calls for a fast deterministic baseline")
    args = parser.parse_args()
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    output_dir = PROJECT_ROOT / "outputs" / "benchmark"
    cache_dir = DATA_DIR = PROJECT_ROOT / "data" / "benchmark_cache"
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    client = OllamaClient()
    installed = set(client.installed_models())
    if not args.rules_only and not installed:
        raise RuntimeError("Ollama is not reachable; start Ollama or use --rules-only")
    rows: list[dict] = []
    model_calls: list[dict] = []
    with SessionLocal() as db:
        for index, case in enumerate(cases, 1):
            requirements = Requirements(case["budget"], case["usage"], case.get("brand"), case["min_storage_gb"], case["details"])
            started = time.perf_counter()
            candidates = load_candidates(db, requirements)
            opinions: list[ModelOpinion] = []
            if candidates and not args.rules_only:
                cache_path = cache_dir / f"{case['id']}.json"
                had_cache = cache_path.exists()
                if had_cache:
                    cached = json.loads(cache_path.read_text(encoding="utf-8"))
                    opinions = [ModelOpinion(**item) for item in cached]
                else:
                    system_prompt, user_prompt = _build_prompt(requirements, candidates)
                    for model in settings.ollama_models:
                        if model in installed:
                            opinion = client.chat_json(model, system_prompt, user_prompt)
                        else:
                            opinion = ModelOpinion(model, False, 0, None, None, None, "", None, "model not installed")
                        opinions.append(opinion)
                    cache_path.write_text(json.dumps([asdict(item) for item in opinions], ensure_ascii=False, indent=2), encoding="utf-8")
                for opinion in opinions:
                    model_calls.append({"case_id": case["id"], "model": opinion.model_name, "success": opinion.success,
                                        "json_success": opinion.parsed is not None, "latency_ms": round(opinion.latency_ms, 1),
                                        "tokens_per_second": opinion.tokens_per_second,
                                        "from_cache": had_cache})
            for mode in MODES:
                if args.rules_only and mode != "rule_only":
                    continue
                ranked = rank_candidates(candidates, requirements, opinions, mode)
                top = ranked[0] if ranked else None
                rows.append({"case_id": case["id"], "mode": mode, "budget": case["budget"], "usage": case["usage"],
                             "required_brand": case.get("brand") or "", "min_storage_gb": case["min_storage_gb"],
                             "candidate_count": len(candidates), "returned_count": len(ranked), "top1": f"{top['brand']} {top['model']} {top['variant']}" if top else "",
                             "top1_price": top["price"] if top else "", "top1_score": top["score"] if top else "",
                             "constraint_pass": compliance(top, case), "expected_result_presence": bool(ranked) == case["expect_results"],
                             "case_latency_ms": round((time.perf_counter() - started) * 1000, 1)})
            print(f"[{index:02d}/{len(cases)}] {case['id']} candidates={len(candidates)}", flush=True)
    csv_path = output_dir / "benchmark-results.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader(); writer.writerows(rows)
    summaries = {}
    for mode in MODES:
        selected = [row for row in rows if row["mode"] == mode]
        if not selected: continue
        summaries[mode] = {
            "cases": len(selected),
            "constraint_accuracy_percent": round(sum(row["constraint_pass"] for row in selected) / len(selected) * 100, 1),
            "result_presence_accuracy_percent": round(sum(row["expected_result_presence"] for row in selected) / len(selected) * 100, 1),
            "mean_top1_score": round(statistics.mean(float(row["top1_score"]) for row in selected if row["top1_score"] != ""), 2),
        }
    fusion_top = {row["case_id"]: row["top1"] for row in rows if row["mode"] == "three_model_fusion" and row["top1"]}
    for mode, summary in summaries.items():
        selected = {row["case_id"]: row["top1"] for row in rows if row["mode"] == mode and row["top1"]}
        comparable = [case_id for case_id in fusion_top if case_id in selected]
        summary["top1_agreement_with_fusion_percent"] = round(
            sum(selected[case_id] == fusion_top[case_id] for case_id in comparable) / len(comparable) * 100, 1
        ) if comparable else 0.0
    model_summary = {}
    for model in settings.ollama_models:
        selected = [row for row in model_calls if row["model"] == model]
        if not selected:
            continue
        latencies = [float(row["latency_ms"]) for row in selected]
        speeds = [float(row["tokens_per_second"]) for row in selected if row["tokens_per_second"]]
        model_summary[model] = {
            "calls": len(selected), "success_percent": round(sum(row["success"] for row in selected) / len(selected) * 100, 1),
            "json_success_percent": round(sum(row["json_success"] for row in selected) / len(selected) * 100, 1),
            "p50_seconds": round(percentile(latencies, .5) / 1000, 2), "p95_seconds": round(percentile(latencies, .95) / 1000, 2),
            "max_seconds": round(max(latencies) / 1000, 2), "mean_tokens_per_second": round(statistics.mean(speeds), 2) if speeds else None,
            "over_180_seconds": sum(value > 180000 for value in latencies),
        }
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "case_count": len(cases), "models": list(settings.ollama_models),
               "rules_only": args.rules_only, "summary": summaries, "model_summary": model_summary, "model_calls": model_calls, "rows": rows}
    json_path = output_dir / "benchmark-results.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md = ["# 30 条固定查询基准与消融实验", "", f"生成时间：{payload['generated_at']}", "",
          "| 方法 | 查询数 | 硬约束正确率 | 有/无结果判定正确率 | 平均 Top1 分 | 与融合Top1一致率 |", "|---|---:|---:|---:|---:|---:|"]
    labels = {"rule_only":"仅规则", "single_qwen":"单模型 Qwen", "three_model_fusion":"三模型融合", "no_consensus":"去除模型共识"}
    for mode, summary in summaries.items():
        md.append(f"| {labels[mode]} | {summary['cases']} | {summary['constraint_accuracy_percent']}% | {summary['result_presence_accuracy_percent']}% | {summary['mean_top1_score']} | {summary['top1_agreement_with_fusion_percent']}% |")
    if model_summary:
        md += ["", "## 模型性能", "", "| 模型 | 调用 | 成功率 | JSON成功率 | P50 | P95 | 最大值 | 输出速度 |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
        for model, summary in model_summary.items():
            md.append(f"| {model} | {summary['calls']} | {summary['success_percent']}% | {summary['json_success_percent']}% | {summary['p50_seconds']}s | {summary['p95_seconds']}s | {summary['max_seconds']}s | {summary['mean_tokens_per_second'] or '—'} tok/s |")
    md += ["", "说明：硬约束包括预算、品牌和存储容量；无结果也是预先定义的正确结果。三模型响应按查询缓存，保证不同消融方法使用完全相同的模型输出。",
           "分数用于同一方法内部排序，不应直接把不同公式的绝对分数解释为准确率。人工相关性评价表见 human-rating-template.csv。", ""]
    (output_dir / "benchmark-report.md").write_text("\n".join(md), encoding="utf-8")
    if summaries:
        width, height = 900, 500
        bars = []
        for index, (mode, summary) in enumerate(summaries.items()):
            value = summary["constraint_accuracy_percent"]
            x = 90 + index * 195; bar_height = value * 3.2; y = 400 - bar_height
            bars.append(f'<rect x="{x}" y="{y}" width="120" height="{bar_height}" rx="12" fill="#176047"/><text x="{x+60}" y="{y-14}" text-anchor="middle" font-size="24" font-weight="700">{value}%</text><text x="{x+60}" y="435" text-anchor="middle" font-size="17">{labels[mode]}</text>')
        svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}"><rect width="100%" height="100%" fill="#f6f4ee"/><text x="50" y="55" font-size="30" font-weight="700" fill="#123e31">30条固定查询：硬约束正确率</text><line x1="55" y1="400" x2="850" y2="400" stroke="#9aa89f"/>{"".join(bars)}<text x="50" y="480" font-size="14" fill="#5d6e65">预算、品牌、最低存储；无符合项时返回空结果也计为正确</text></svg>'
        (output_dir / "constraint-accuracy.svg").write_text(svg, encoding="utf-8")
    human = output_dir / "human-rating-template.csv"
    with human.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle); writer.writerow(["case_id", "mode", "top1", "需求相关性(1-5)", "理由充分性(1-5)", "可解释性(1-5)", "评审备注"])
        for row in rows: writer.writerow([row["case_id"], row["mode"], row["top1"], "", "", "", ""])
    print(json.dumps(summaries, ensure_ascii=False, indent=2)); print(f"Report: {(output_dir / 'benchmark-report.md').resolve()}")


if __name__ == "__main__":
    main()

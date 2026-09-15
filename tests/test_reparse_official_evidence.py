import importlib.util
import json
import os
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "reparse_official_evidence.py"
SPEC = importlib.util.spec_from_file_location("reparse_official_evidence", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_rebuild_items_uses_newest_successful_evidence_across_reports(tmp_path) -> None:
    old_text = tmp_path / "old.txt"
    new_text = tmp_path / "new.txt"
    old_text.write_text("12GB+256GB", encoding="utf-8")
    new_text.write_text("16GB+512GB", encoding="utf-8")
    old_report = tmp_path / "catalog-old.json"
    new_report = tmp_path / "catalog-new.json"
    base = {"brand": "测试品牌", "model_name": "测试机", "url": "https://example.test/specs"}
    old_report.write_text(json.dumps({"items": [{**base, "evidence_path": str(old_text)}]}), encoding="utf-8")
    new_report.write_text(json.dumps({"items": [{**base, "evidence_path": str(new_text)}]}), encoding="utf-8")
    os.utime(old_report, (1, 1))
    os.utime(new_report, (2, 2))
    source = tmp_path / "sources.csv"
    source.write_text(
        "brand,model_name,official_url,fallback_variants,source_note\n"
        "测试品牌,测试机,https://example.test/specs,,测试\n",
        encoding="utf-8-sig",
    )

    collected, failures = MODULE.rebuild_items([new_report, old_report], [source])
    assert failures == []
    assert len(collected) == 1
    assert collected[0].evidence_path == str(new_text.resolve())
    assert collected[0].variants[0]["ram_gb"] == 16
    assert collected[0].variants[0]["storage_gb"] == 512

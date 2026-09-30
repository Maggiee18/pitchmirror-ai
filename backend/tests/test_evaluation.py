"""Guards the evaluation benchmark numbers reported in docs/evaluation.md against regressions."""
import asyncio
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("evaluate", Path(__file__).resolve().parents[2] / "scripts" / "evaluate.py")
evaluate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluate)


def test_benchmark_thresholds(tmp_path, monkeypatch):
    res = asyncio.run(evaluate.main(use_ai=False))
    tuning, holdout = res["analysis"][0], res["analysis"][1]
    assert tuning["exact_match"] == tuning["cases"]
    assert holdout["recall"] >= 0.8 and holdout["precision"] >= 0.8
    assert res["grounding"]["fabricated_rejected"] == res["grounding"]["fabricated"]
    assert res["grounding"]["genuine_kept"]
    assert res["delivery"]["passed"] == res["delivery"]["total"]

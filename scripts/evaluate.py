"""PitchMirror evaluation benchmark.

Runs labelled presenter scenarios through the REAL analysis pipeline and reports detection precision/recall,
the grounding filter's rejection of fabricated AI claims, delivery metric accuracy, and latency.

    python scripts/evaluate.py              # rule engine (deterministic, no API key needed)
    python scripts/evaluate.py --ai         # also run with the configured LLM (uses your API key)

Writes docs/evaluation.md and docs/evaluation.json.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.agents import llm as llm_mod  # noqa: E402
from app.agents.llm import LLMProvider  # noqa: E402
from app.agents.slide_analyzer import analyze_slide, validate_feedback  # noqa: E402
from app.analysis.delivery import compute_delivery  # noqa: E402
from app.ingest import parse_presentation  # noqa: E402
from app.models import PauseEvent, SlideContext, TranscriptSegment  # noqa: E402

CATS = ["slide_speech_consistency", "reading_slide", "off_topic", "visual_gap"]

# (name, slide number in the sample deck, transcript, set of issue categories that SHOULD be flagged)
CASES = [
    ("wrong percentage", 4, "Our YOLOv8 model improves the accuracy by 25 percent over YOLOv5 and it runs really fast, "
     "so as the bar chart shows it clearly beats Faster R-CNN on our crowd test set.", {"slide_speech_consistency"}),
    ("correct percentage", 4, "As the bar chart shows, YOLOv8s improves mAP by 13 percent over YOLOv5s, and it runs at 38 FPS "
     "compared with only 9 FPS for Faster R-CNN, after training on 4,200 annotated frames.", set()),
    ("spoken number words", 4, "Looking at the chart, YOLOv8s improves mAP by thirteen percent over YOLOv5s. It runs at "
     "thirty eight FPS versus nine FPS for Faster R-CNN, and we trained on 4,200 annotated frames.", set()),
    ("wrong table number", 5, "In this table CSRNet reduces the error on ShanghaiTech A by 70 percent compared to MCNN, "
     "and risk alerts reach 92% precision and 87% recall on 36 incident clips.", {"slide_speech_consistency"}),
    ("wrong fps", 3, "The whole pipeline in this architecture diagram runs at 60 FPS on an RTX 3060, from the CCTV stream "
     "through the YOLOv8s detector, DeepSORT tracker and CSRNet density map to the risk scorer.", {"slide_speech_consistency"}),
    ("read verbatim", 2, "Crowd crushes caused more than 1,400 deaths worldwide between 2010 and 2023. Manual CCTV monitoring: "
     "one operator watches around 40 camera feeds. Existing systems count people but do not predict dangerous density build up.",
     {"reading_slide"}),
    ("explained in own words", 2, "Stampedes at festivals and stadiums have killed well over a thousand people since 2010, "
     "and a single control room operator can't realistically watch forty screens, so we want early warnings a full minute "
     "before crowd density becomes dangerous.", set()),
    ("off topic", 6, "Honestly our team had a great time at the hackathon, the food was amazing and we met lots of mentors "
     "who gave us advice on pitching, marketing, fundraising and hiring for a future startup company in Bangalore someday.",
     {"off_topic"}),
    ("limitations covered", 6, "Our main limitations are night time footage, where recall drops to 0.61, and heavy occlusion above "
     "8 people per square metre, which breaks tracking. Next we want thermal cameras and edge deployment on a Jetson Orin.",
     set()),
    ("diagram ignored", 3, "So basically our system is very fast and very accurate, it runs at 38 FPS on an RTX 3060, and people "
     "found it useful in testing, and we think it can save lives at big public events in India.", {"visual_gap"}),
    ("diagram explained", 3, "In this architecture diagram the CCTV stream goes into the YOLOv8s person detector, then the DeepSORT "
     "tracker, then the CSRNet density map, and optical flow from Farneback feeds the risk scorer which raises alerts, all at 38 FPS.",
     set()),
    ("table ignored", 5, "Risk alerts reached 92% precision and 87% recall on 36 annotated incident clips, which we are really "
     "happy about because false alarms would annoy the operators a lot in real deployments.", {"visual_gap"}),
    ("table explained", 5, "This table compares mean absolute error: CSRNet cuts error versus MCNN on every dataset, from 110.2 to "
     "68.2 on ShanghaiTech A, 26.4 to 10.6 on ShanghaiTech B and 277 to 135.4 on UCF-QNRF. Alerts hit 92% precision.", set()),
]

# Hold-out scenarios: written after the rules were tuned on CASES and reported as-is, to show generalisation honestly.
HOLDOUT = [
    ("wrong recall value", 6, "At night the recall actually goes up to 0.91, and occlusion above 8 people per square metre is "
     "handled fine, so next we move to thermal cameras and a Jetson Orin for edge deployment.", {"slide_speech_consistency"}),
    ("wrong training size", 4, "Looking at the chart, YOLOv8s improves mAP by 13% over YOLOv5s, runs at 38 FPS versus 9 FPS, "
     "and we trained it on about 12,000 annotated frames from our own cameras.", {"slide_speech_consistency"}),
    ("number said in words", 2, "Roughly fourteen hundred people died in crowd crushes since 2010, and one operator has to watch "
     "about forty camera feeds, which is why we want an alert a full minute before density gets dangerous.", set()),
    ("bullets read aloud", 6, "Night time footage reduces detection recall to 0.61. Heavy occlusion above 8 people per square "
     "metre breaks tracking. Future: thermal cameras, edge deployment on Jetson Orin.", {"reading_slide"}),
    ("chart only gestured at", 4, "Our results are here and they are really good, we beat everything else and it is the best "
     "model overall in every way possible for this problem, honestly we are very proud of it.", {"visual_gap"}),
    ("off topic on diagram slide", 3, "Let me tell you about our team budget, we spent most of the money on pizza and travel "
     "and hotel rooms, and next year we will apply for more sponsorship from companies and alumni.", {"off_topic", "visual_gap"}),
]

# Fabricated model output: quotes that do not exist in the slide or transcript must be rejected.
FABRICATED = [
    {"kind": "warning", "category": "slide_speech_consistency", "severity": "high", "observation": "Invented mismatch",
     "evidence_slide": "Latency is 5 ms on Raspberry Pi", "evidence_speech": "we deployed it on a Raspberry Pi cluster"},
    {"kind": "warning", "category": "unsupported_claim", "severity": "medium", "observation": "Invented claim",
     "evidence_slide": "", "evidence_speech": "our model is approved by the government"},
    {"kind": "improvement", "category": "visual_gap", "severity": "medium", "observation": "Invented visual",
     "evidence_slide": "Pie chart of market share by region", "evidence_speech": ""},
    {"kind": "improvement", "category": "missed_concept", "severity": "medium", "observation": "Invented concept",
     "evidence_slide": "Blockchain based audit trail", "evidence_speech": ""},
]
GENUINE = {"kind": "warning", "category": "slide_speech_consistency", "severity": "high", "observation": "Real mismatch",
           "evidence_slide": "YOLOv8s improves mAP by 13% over YOLOv5s", "evidence_speech": "improves the accuracy by 25 percent over YOLOv5"}


async def run_cases(slides: list[SlideContext], label: str, cases=None) -> dict:
    cases = cases if cases is not None else CASES
    tp = {c: 0 for c in CATS}
    fp = {c: 0 for c in CATS}
    fn = {c: 0 for c in CATS}
    rows, latencies = [], []
    outline = [f"{s.slide_number}. {s.title}" for s in slides]
    for name, n, transcript, expected in cases:
        t0 = time.perf_counter()
        res = await analyze_slide(slides[n - 1], transcript, "viva", [], outline, final=True)
        latencies.append((time.perf_counter() - t0) * 1000)
        got = {f.category for f in res.feedback if f.kind != "positive"} & set(CATS)
        for c in CATS:
            if c in got and c in expected:
                tp[c] += 1
            elif c in got:
                fp[c] += 1
            elif c in expected:
                fn[c] += 1
        rows.append({"case": name, "slide": n, "expected": sorted(expected), "flagged": sorted(got),
                     "ok": expected <= got and not (got - expected)})
    per_cat = {}
    for c in CATS:
        p = tp[c] / (tp[c] + fp[c]) if tp[c] + fp[c] else None
        r = tp[c] / (tp[c] + fn[c]) if tp[c] + fn[c] else None
        per_cat[c] = {"precision": p, "recall": r, "tp": tp[c], "fp": fp[c], "fn": fn[c]}
    all_tp, all_fp, all_fn = sum(tp.values()), sum(fp.values()), sum(fn.values())
    lat = sorted(latencies)
    return {
        "engine": label,
        "cases": len(cases),
        "exact_match": sum(r["ok"] for r in rows),
        "precision": all_tp / (all_tp + all_fp) if all_tp + all_fp else None,
        "recall": all_tp / (all_tp + all_fn) if all_tp + all_fn else None,
        "per_category": per_cat,
        "latency_ms": {"median": round(lat[len(lat) // 2], 1), "max": round(lat[-1], 1)},
        "rows": rows,
    }


def grounding_eval(slides: list[SlideContext]) -> dict:
    transcript = CASES[0][2]
    items, rejected = validate_feedback(FABRICATED + [GENUINE], slides[3], transcript, final=True)
    kept = [i.observation for i in items]
    return {"fabricated": len(FABRICATED), "fabricated_rejected": sum(1 for f in FABRICATED if f["observation"] not in kept),
            "genuine_kept": "Real mismatch" in kept}


def delivery_eval() -> dict:
    words = " ".join(["word"] * 140) + " um so um basically uh this works"
    segs = [TranscriptSegment(seq=1, text=words, slide_number=1, t_start=0, t_end=60)]
    pauses = [PauseEvent(slide_number=1, t=10, duration_s=2.0), PauseEvent(slide_number=1, t=30, duration_s=5.0)]
    raw = compute_delivery(segs, pauses, {1: 60.0}, {1: 75.0}, 75.0)["raw"]
    checks = {
        "wpm (expected 147)": raw["wpm"] == 147.0,
        "fillers (expected 4)": raw["filler_total"] == 4,
        "pauses >=1.5s (expected 2)": raw["pause_count"] == 2,
        "long pauses >=4s (expected 1)": raw["long_pause_count"] == 1,
    }
    return {"checks": checks, "passed": sum(checks.values()), "total": len(checks)}


def pct(x):
    return "n/a" if x is None else f"{x * 100:.0f}%"


def to_markdown(results: list[dict], grounding: dict, delivery: dict) -> str:
    out = ["# PitchMirror evaluation", "",
           f"Generated by `scripts/evaluate.py` on {time.strftime('%Y-%m-%d %H:%M')}. "
           f"{len(CASES)} tuning and {len(HOLDOUT)} hold-out labelled presenter scenarios on the sample viva deck, run through the real analysis pipeline.", ""]
    for r in results:
        out += [f"## Slide/speech analysis: {r['engine']}", "",
                f"- Scenarios fully correct: **{r['exact_match']}/{r['cases']}**",
                f"- Issue detection precision **{pct(r['precision'])}**, recall **{pct(r['recall'])}**",
                f"- Analysis latency: median {r['latency_ms']['median']} ms, max {r['latency_ms']['max']} ms", "",
                "| Category | Precision | Recall |", "|---|---|---|"]
        for c, v in r["per_category"].items():
            out.append(f"| {c.replace('_', ' ')} | {pct(v['precision'])} | {pct(v['recall'])} |")
        out += ["", "| Scenario | Slide | Expected | Flagged | OK |", "|---|---|---|---|---|"]
        for row in r["rows"]:
            out.append(f"| {row['case']} | {row['slide']} | {', '.join(row['expected']) or 'none'} | "
                       f"{', '.join(row['flagged']) or 'none'} | {'✅' if row['ok'] else '❌'} |")
        out.append("")
    out += ["## Grounding filter (anti-hallucination)", "",
            f"- Fabricated AI claims rejected: **{grounding['fabricated_rejected']}/{grounding['fabricated']}**",
            f"- Genuine, correctly quoted claim kept: **{'yes' if grounding['genuine_kept'] else 'no'}**", "",
            "## Delivery metrics (deterministic)", "",
            f"- Checks passed: **{delivery['passed']}/{delivery['total']}**", ""]
    out += [f"- {k}: {'pass' if v else 'FAIL'}" for k, v in delivery["checks"].items()]
    out += ["", "Scores and categories are measured on a small hand-labelled set; they show the system behaves as designed, "
            "not a validated accuracy on real presentations."]
    return "\n".join(out) + "\n"


async def main(use_ai: bool) -> dict:
    slides = parse_presentation(ROOT / "samples" / "crowdsense_viva_demo.pdf", "pdf", Path(tempfile.mkdtemp()), 640, 60)
    llm_mod.set_llm(LLMProvider("offline", "", supports_vision=False))
    results = [await run_cases(slides, "rule engine (no API key), tuning set"),
               await run_cases(slides, "rule engine (no API key), hold-out set", HOLDOUT)]
    if use_ai:
        llm_mod._provider = None  # rebuild from .env
        prov = llm_mod.get_llm()
        if prov.available:
            results.append(await run_cases(slides, f"AI ({prov.name} {prov.model}), all scenarios", CASES + HOLDOUT))
        else:
            print("No API key configured; skipping AI run.")
    grounding = grounding_eval(slides)
    delivery = delivery_eval()
    (ROOT / "docs").mkdir(exist_ok=True)
    (ROOT / "docs" / "evaluation.md").write_text(to_markdown(results, grounding, delivery), encoding="utf-8")
    (ROOT / "docs" / "evaluation.json").write_text(json.dumps({"analysis": results, "grounding": grounding, "delivery": delivery},
                                                              indent=2), encoding="utf-8")
    return {"analysis": results, "grounding": grounding, "delivery": delivery}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ai", action="store_true", help="also evaluate with the configured LLM")
    res = asyncio.run(main(ap.parse_args().ai))
    for r in res["analysis"]:
        print(f"{r['engine']}: {r['exact_match']}/{r['cases']} scenarios, precision {pct(r['precision'])}, recall {pct(r['recall'])}")
    g = res["grounding"]
    print(f"grounding: rejected {g['fabricated_rejected']}/{g['fabricated']} fabricated, genuine kept: {g['genuine_kept']}")
    print(f"delivery: {res['delivery']['passed']}/{res['delivery']['total']} checks")

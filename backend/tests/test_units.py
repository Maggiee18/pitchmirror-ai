from pathlib import Path

import pytest

from app.agents.audience import rule_evaluate, rule_question
from app.agents.llm import LLMError, extract_json
from app.agents.slide_analyzer import validate_feedback
from app.analysis.delivery import compute_delivery, count_fillers, repetitions
from app.analysis.evidence import compute_evidence
from app.ingest import detect_kind, parse_presentation
from app.models import PauseEvent, Question, SlideContext, TranscriptSegment, VisualElement

SAMPLES = Path(__file__).resolve().parents[2] / "samples"


def seg(text, slide=1, t0=0.0, t1=10.0, seq=1):
    return TranscriptSegment(seq=seq, text=text, slide_number=slide, t_start=t0, t_end=t1)


def test_fillers_and_repetition():
    c = count_fillers("um so basically um our model uh you know works")
    assert c["um"] == 2 and c["uh"] == 1 and c["basically"] == 1 and c["you know"] == 1
    r = repetitions("the crowd density map the crowd density map the crowd density map is is good")
    assert r["repeated_phrases"][0]["phrase"] == "the crowd density"
    assert r["immediate_repeats"][0]["word"] == "is"


def test_delivery_metrics_measured_not_invented():
    d = compute_delivery([], [], {}, {}, 0)
    assert d["raw"]["wpm"] is None and d["raw"]["speaking_time_source"] == "not measured"
    segs = [seg(" ".join(["word"] * 150), t0=0, t1=60)]
    d = compute_delivery(segs, [PauseEvent(slide_number=1, t=5, duration_s=5.0)], {1: 60.0}, {1: 70.0}, 70)
    assert d["raw"]["wpm"] == 150.0
    assert d["raw"]["long_pause_count"] == 1
    assert d["raw"]["speaking_time_source"] == "microphone voice activity"


def test_number_conflict_detected_and_grounded():
    slide = SlideContext(slide_number=3, title="Results", text=["Revenue increased 42% after introducing feature X"],
                         key_concepts=["Revenue increased 42% after introducing feature X"])
    ev = compute_evidence(slide, "Our revenue increased by 10 percent because of the marketing campaign we ran")
    assert ev["possible_number_conflicts"], ev
    assert ev["possible_number_conflicts"][0]["slide_value"] == "42%"
    ev2 = compute_evidence(slide, "Our revenue increased by forty two percent after we introduced feature X")
    assert not ev2["possible_number_conflicts"]


def test_visual_gap_evidence():
    slide = SlideContext(slide_number=2, title="Growth", text=["We grew"],
                         visual_elements=[VisualElement(kind="chart", description="Bar chart of monthly revenue",
                                                        data_summary="Jan 10, Feb 12, Mar 20")])
    ev = compute_evidence(slide, "our product has grown significantly and customers love it a lot these days honestly and truly")
    assert ev["visuals"][0]["status"] == "no"
    ev = compute_evidence(slide, "as the bar chart shows monthly revenue doubled from January to March, peaking in March")
    assert ev["visuals"][0]["status"] in ("yes", "partial")


def test_grounding_rejects_hallucinated_quotes():
    slide = SlideContext(slide_number=1, title="Results", text=["Accuracy is 92% on the test set"])
    transcript = "we got about eighty percent accuracy on validation"
    raw = [
        {"kind": "warning", "category": "slide_speech_consistency", "severity": "high", "observation": "Mismatch",
         "evidence_slide": "Accuracy is 92% on the test set", "evidence_speech": "eighty percent accuracy on validation"},
        {"kind": "warning", "category": "slide_speech_consistency", "severity": "high", "observation": "Invented",
         "evidence_slide": "Latency is 5 ms on edge devices", "evidence_speech": "we deploy on raspberry pi clusters"},
        {"kind": "bogus", "category": "x", "observation": "bad schema"},
    ]
    items, rejected = validate_feedback(raw, slide, transcript, final=True)
    assert len(items) == 1 and items[0].observation == "Mismatch"
    assert rejected == 2


def test_extract_json_tolerates_fences():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Sure! {"a": 2} hope that helps') == {"a": 2}
    with pytest.raises(LLMError):
        extract_json("no json here")


def test_rule_question_is_grounded():
    slide = SlideContext(slide_number=4, title="Detection Results", text=["YOLOv8s improves mAP by 13% over YOLOv5s"],
                         key_concepts=["YOLOv8s improves mAP by 13% over YOLOv5s"])
    q = rule_question(slide, "our yolov8 model improves the accuracy by 25 percent over yolov5 which is great", [], [], "viva")
    assert q and "13%" in q.question


def test_rule_evaluate_and_followup():
    q = Question(slide_number=1, question="Why did you choose YOLOv8 over Faster R-CNN?")
    ev, follow = rule_evaluate(q, "because YOLOv8 runs at 38 FPS while Faster R-CNN only reaches 9 FPS on our GPU, so real time alerts are possible")
    assert ev.score >= 4 and follow is not None and follow.depth == 1
    ev, follow = rule_evaluate(q, "um")
    assert ev.score == 0 and follow is None


def test_upload_validation():
    with pytest.raises(ValueError):
        detect_kind("evil.exe", b"MZ")
    with pytest.raises(ValueError):
        detect_kind("fake.pdf", b"hello world")
    with pytest.raises(ValueError):
        detect_kind("old.ppt", b"\xd0\xcf")
    assert detect_kind("ok.pdf", b"%PDF-1.7") == "pdf"


def test_parse_sample_pdf(tmp_path):
    slides = parse_presentation(SAMPLES / "crowdsense_viva_demo.pdf", "pdf", tmp_path, 800, 60)
    assert len(slides) == 6
    kinds = {v.kind for s in slides for v in s.visual_elements}
    assert {"diagram", "chart", "table"} <= kinds
    assert all((tmp_path / s.image_file).exists() for s in slides)


def test_parse_pptx(tmp_path):
    slides = parse_presentation(SAMPLES / "growth_pitch_demo.pptx", "pptx", tmp_path, 800, 60)
    assert slides[0].notes and any(v.kind == "chart" and "42" in v.data_summary for v in slides[0].visual_elements)

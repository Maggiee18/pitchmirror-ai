"""Answer Intelligence + Try Again (additive feature)."""
import pytest
from fastapi.testclient import TestClient

from app.agents import answer_intel as ai
from app.agents import llm as llm_mod
from app.agents.llm import LLMError, LLMProvider
from app.main import app
from app.models import AnswerAttempt, AnswerIntelligence, AnswerScores, SlideContext
from tests.test_integration import FakeLLM, read_until

SLIDE = SlideContext(slide_number=4, title="Detection Results",
                     text=["YOLOv8s improves mAP by 13% over YOLOv5s", "Runs at 38 FPS vs 9 FPS for Faster R-CNN"])


def test_rule_intel_short_answer():
    r = ai.rule_intel("Why did you choose YOLOv8?", "um", SLIDE)
    assert r.assessment == "insufficient_evidence" and r.retry_recommended and r.scaffold_question
    assert r.scores.correctness is None


def test_rule_intel_measures_only_what_it_can():
    r = ai.rule_intel("Why did you choose YOLOv8 over Faster R-CNN?",
                      "Because YOLOv8 runs at 38 FPS while Faster R-CNN only reaches 9 FPS, so real time alerts are possible", SLIDE)
    assert r.assessment == "unable_to_determine"
    assert r.scores.correctness is None and r.scores.relevance is not None
    assert "why" in r.justification and "evidence" in r.justification
    assert r.better_answer == ""  # never invented without the AI model


def test_better_answer_grounding_withholds_invented_facts():
    ctx = "our YOLOv8 model improves accuracy and runs in real time"
    data = {"assessment": "mostly_correct", "scores": {"correctness": 80, "relevance": 90, "completeness": 60, "technical_depth": 70, "clarity": 85},
            "better_answer": "We chose YOLOv8 and deployed it on Kubernetes, reaching 99.5% accuracy.", "why_better": ["x"]}
    r = ai.validate_intel(data, "Why YOLOv8?", "YOLOv8 is faster", SLIDE, ctx)
    assert r.better_answer == "" and r.why_better == [] and "withheld" in r.note

    data["better_answer"] = "We chose YOLOv8 because we need real time detection: it runs at 38 FPS versus 9 FPS for Faster R-CNN."
    r = ai.validate_intel(data, "Why YOLOv8?", "YOLOv8 is faster", SLIDE, ctx)
    assert r.better_answer.startswith("We chose YOLOv8") and r.why_better == ["x"]


def test_validate_clamps_and_respects_unable_to_determine():
    r = ai.validate_intel({"assessment": "unable_to_determine", "scores": {"correctness": 140, "relevance": "70", "clarity": -5}},
                          "q", "a b c", SLIDE, "")
    assert r.scores.correctness is None and r.scores.relevance == 70 and r.scores.clarity == 0
    r = ai.validate_intel({"assessment": "bogus"}, "q", "a b c", SLIDE, "")
    assert r.assessment == "unable_to_determine"


def test_compare_attempts():
    def att(n, src, **sc):
        return AnswerAttempt(attempt=n, answer_text="x", intel=AnswerIntelligence(source=src, scores=AnswerScores(**sc)))
    c = ai.compare_attempts(att(1, "ai", correctness=60, relevance=60, completeness=50),
                            att(2, "ai", correctness=80, relevance=90, completeness=80))
    assert c["comparable"] and c["before"] == 57 and c["after"] == 83 and c["delta"] == 26
    c = ai.compare_attempts(att(1, "rule", relevance=40), att(2, "ai", correctness=80, relevance=90, completeness=80))
    assert not c["comparable"]


class IntelLLM(FakeLLM):
    async def generate_json(self, system, prompt, images=None, max_tokens=1200, timeout=None, temperature=0.3):
        if "coach the presenter on ONE spoken answer" in system:
            second = "Answer attempt 2" in prompt
            return {"assessment": "mostly_correct" if second else "partially_correct",
                    "scores": {"correctness": 85 if second else 60, "relevance": 90 if second else 55, "completeness": 80 if second else 40,
                               "technical_depth": 75 if second else 45, "clarity": 85 if second else 70},
                    "what_was_good": ["Named the metric"], "what_was_missing": [] if second else ["No baseline named"],
                    "better_answer": "The 13% is the mAP gain of YOLOv8s over YOLOv5s on our test split.",
                    "why_better": ["States the baseline"], "retry_recommended": not second,
                    "scaffold_question": "" if second else "Which model did you compare against?"}
        return await super().generate_json(system, prompt, images, max_tokens, timeout, temperature)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _answer_and_retry(client):
    sid = client.post("/api/samples/crowdsense_viva_demo.pdf", data={"mode": "viva"}).json()["id"]
    with client.websocket_connect(f"/ws/{sid}") as ws:
        ws.receive_json()
        ws.send_json({"type": "start"})
        ws.send_json({"type": "slide", "slide_number": 4})
        ws.send_json({"type": "transcript", "seq": 1, "slide_number": 4, "t_start": 0, "t_end": 10,
                      "text": "our YOLOv8 model improves the accuracy by 25 percent over YOLOv5 and it runs really fast on crowds"})
        ws.send_json({"type": "ask_question", "slide_number": 4})
        q, _ = read_until(ws, lambda m: m["type"] == "question")
        qid = q["item"]["id"]
        ws.send_json({"type": "transcript", "seq": 2, "kind": "answer", "question_id": qid, "slide_number": 4,
                      "t_start": 11, "t_end": 14, "text": "it is the accuracy improvement we measured"})
        ws.send_json({"type": "answer_done", "question_id": qid})
        first, _ = read_until(ws, lambda m: m["type"] == "answer_intel" and m["attempt"] == 1)
        ws.send_json({"type": "transcript", "seq": 3, "kind": "answer", "question_id": qid, "attempt": 2, "slide_number": 4,
                      "t_start": 20, "t_end": 26, "text": "the 13 percent is the mAP gain of YOLOv8s over YOLOv5s measured on our test split"})
        ws.send_json({"type": "retry_done", "question_id": qid, "attempt": 2})
        second, _ = read_until(ws, lambda m: m["type"] == "answer_intel" and m["attempt"] == 2)
        ws.send_json({"type": "end"})
        read_until(ws, lambda m: m["type"] == "report_ready")
    return sid, first, second


def test_ai_answer_intel_and_retry(client):
    llm_mod.set_llm(IntelLLM())
    sid, first, second = _answer_and_retry(client)
    a1 = first["item"]["attempts"][0]
    assert a1["answer_text"] == "it is the accuracy improvement we measured"
    assert a1["intel"]["assessment"] == "partially_correct" and a1["intel"]["better_answer"]
    atts = second["item"]["attempts"]
    assert [a["attempt"] for a in atts] == [1, 2]
    assert "13 percent" in atts[1]["answer_text"] and "accuracy improvement" not in atts[1]["answer_text"]
    assert second["comparison"]["comparable"] and second["comparison"]["delta"] > 0
    # the original evaluation is untouched and still based on attempt 1 only
    q = client.get(f"/api/sessions/{sid}").json()["questions"][0]
    assert q["answer_text"] == "it is the accuracy improvement we measured" and q["evaluation"] is not None


def test_offline_answer_intel_and_failure(client):
    llm_mod.set_llm(LLMProvider("offline", "", supports_vision=False))
    _, first, second = _answer_and_retry(client)
    assert first["item"]["attempts"][0]["intel"]["source"] == "rule"
    assert second["comparison"] is not None  # comparable on relevance or explicitly not comparable

    class Broken(FakeLLM):
        async def generate_json(self, system, prompt, *a, **k):
            if "ONE spoken answer" in system:
                raise LLMError("HTTP 500")
            return await super().generate_json(system, prompt, *a, **k)
    llm_mod.set_llm(Broken())
    _, first, _ = _answer_and_retry(client)
    assert "AI analysis unavailable" in first["item"]["attempts"][0]["intel"]["note"]

"""End to end: upload -> websocket session -> transcript -> feedback -> question -> answer -> follow up -> report."""
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.agents import llm as llm_mod
from app.agents.llm import LLMError, LLMProvider
from app.main import app

SAMPLES = Path(__file__).resolve().parents[2] / "samples"


class FakeLLM(LLMProvider):
    """Deterministic stand in for a real model. Includes one hallucinated item that must be rejected."""

    def __init__(self, fail=False):
        super().__init__("fake", "fake-1")
        self.fail = fail
        self.prompts = []

    async def generate_json(self, system, prompt, images=None, max_tokens=1200, timeout=None, temperature=0.3):
        self.prompts.append((system, prompt))
        if self.fail:
            raise LLMError("HTTP 500")
        if system.startswith("You describe presentation slides"):
            return {"visual_description": "A slide.", "visual_elements": [], "key_concepts": [], "important_elements": []}
        if "real-time presentation coach" in system:
            return {
                "covered_concepts": ["YOLOv8s"], "missed_concepts": ["Runs at 38 FPS vs 9 FPS for Faster R-CNN"],
                "visuals": [{"element": "bar chart", "explained": "no", "evidence": ""}],
                "feedback": [
                    {"kind": "warning", "category": "slide_speech_consistency", "severity": "high",
                     "observation": "You said 25 percent but the slide says 13%.",
                     "evidence_slide": "YOLOv8s improves mAP by 13% over YOLOv5s",
                     "evidence_speech": "improves the accuracy by 25 percent over yolov5",
                     "explanation": "Numbers differ.", "suggestion": "Use the slide number."},
                    {"kind": "warning", "category": "slide_speech_consistency", "severity": "high",
                     "observation": "Hallucinated", "evidence_slide": "Revenue doubled in Europe",
                     "evidence_speech": "we launched in Germany", "explanation": "", "suggestion": ""},
                ],
            }
        if "ONE sharp, specific question" in system:
            return {"question": "Is the 13% an absolute or relative mAP gain, and on which split?", "reason": "mismatch", "difficulty": "hard"}
        if "evaluate a spoken answer" in system:
            return {"score": 3, "addressed_question": True, "strengths": ["gave numbers"], "gaps": ["no split named"],
                    "summary": "Adequate.", "follow_up": {"question": "How many images were in that test split, and how was it sampled?", "reason": "depth", "difficulty": "hard"}}
        if "final coaching report" in system:
            return {"summary": "Solid.", "top_recommendations": [{"title": "Fix numbers", "detail": "Say 13% on slide 4", "evidence": "mismatch"}],
                    "practice_questions": [{"question": "Why CSRNet?", "slide_number": 5, "why": "design"}]}
        return {}


def read_until(ws, pred, limit=200):
    seen = []
    for _ in range(limit):
        m = ws.receive_json()
        seen.append(m)
        if pred(m):
            return m, seen
    raise AssertionError(f"condition not met; saw types {[m['type'] for m in seen]}")


def run_session(client, fake=None):
    r = client.post("/api/samples/crowdsense_viva_demo.pdf", data={"mode": "viva"})
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    with client.websocket_connect(f"/ws/{sid}") as ws:
        snap = ws.receive_json()
        assert snap["type"] == "snapshot" and len(snap["session"]["slides"]) == 6
        ws.send_json({"type": "start"})
        read_until(ws, lambda m: m["type"] == "status" and m["status"] == "live")
        ws.send_json({"type": "slide", "slide_number": 4})
        ws.send_json({"type": "activity", "slide_number": 4, "voiced_s": 14})
        ws.send_json({"type": "pause", "duration_s": 2.2})
        text = ("um so our YOLOv8 model improves the accuracy by 25 percent over YOLOv5 and it is really fast, "
                "we trained it on a lot of frames and it works well for dense crowds in practice")
        ws.send_json({"type": "transcript", "seq": 1, "text": text, "slide_number": 4, "t_start": 1, "t_end": 13})
        read_until(ws, lambda m: m["type"] == "ack" and m["seq"] == 1)
        # duplicate after a reconnect must be ignored
        ws.send_json({"type": "transcript", "seq": 1, "text": text, "slide_number": 4, "t_start": 1, "t_end": 13})
        # move on -> final analysis of slide 4 -> feedback + auto question
        ws.send_json({"type": "slide", "slide_number": 5})
        fb, seen = read_until(ws, lambda m: m["type"] == "feedback" and m["item"]["category"] == "slide_speech_consistency")
        assert "13%" in fb["item"]["evidence_slide"]
        q, seen2 = read_until(ws, lambda m: m["type"] == "question")
        qid = q["item"]["id"]
        ws.send_json({"type": "transcript", "seq": 2, "kind": "answer", "question_id": qid, "slide_number": 5,
                      "text": "because the 13 percent is the relative gain measured on our 600 image dense crowd test split",
                      "t_start": 20, "t_end": 26})
        ws.send_json({"type": "answer_done", "question_id": qid})
        upd, _ = read_until(ws, lambda m: m["type"] == "question_update" and m["item"]["status"] == "answered")
        assert upd["item"]["evaluation"]["score"] is not None
        follow, _ = read_until(ws, lambda m: m["type"] == "question")
        assert follow["item"]["depth"] == 1 and follow["item"]["parent_id"] == qid
        ws.send_json({"type": "not_a_real_type"})
        read_until(ws, lambda m: m["type"] == "notice")
        ws.send_json({"type": "end"})
        read_until(ws, lambda m: m["type"] == "report_ready")
    rep = client.get(f"/api/sessions/{sid}/report").json()
    assert rep["status"] == "ready"
    return sid, rep["report"], seen + seen2


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_offline_end_to_end(client):
    llm_mod.set_llm(LLMProvider("offline", "", supports_vision=False))
    sid, report, _ = run_session(client)
    assert report["overview"]["slides_presented"] == 1
    assert report["delivery"]["raw"]["filler_total"] >= 1
    assert report["delivery"]["raw"]["wpm"] is not None
    assert report["consistency"]["mismatches"]
    assert any(s["value"] is not None for s in report["scores"])
    assert report["narrative"]["source"] == "rule"
    # refresh/restart
    assert client.get(f"/api/sessions/{sid}").status_code == 200
    assert client.post(f"/api/sessions/{sid}/restart").status_code == 200
    s = client.get(f"/api/sessions/{sid}").json()
    assert s["status"] == "ready" and not s["segments"]
    assert client.delete(f"/api/sessions/{sid}").status_code == 200
    assert client.get(f"/api/sessions/{sid}").status_code == 404


def test_ai_end_to_end_with_grounding(client):
    fake = FakeLLM()
    llm_mod.set_llm(fake)
    sid, report, seen = run_session(client, fake)
    obs = [f["observation"] for f in report["feedback"]]
    assert "Hallucinated" not in obs
    assert report["consistency"]["rejected_ungrounded_items"] >= 1
    assert report["narrative"]["source"] == "ai"
    qs = report["questions"]["asked"]
    assert qs[0]["source"] == "ai" and qs[0]["answer_text"]


def test_llm_failure_falls_back(client):
    llm_mod.set_llm(FakeLLM(fail=True))
    sid, report, _ = run_session(client)
    assert report["consistency"]["mismatches"]  # rule based conflict still found
    assert report["narrative"]["source"] == "rule"


def test_upload_errors(client):
    llm_mod.set_llm(LLMProvider("offline", "", supports_vision=False))
    r = client.post("/api/upload", files={"file": ("x.pdf", b"not a pdf at all", "application/pdf")}, data={"mode": "viva"})
    assert r.status_code == 400
    r = client.post("/api/upload", files={"file": ("x.pdf", b"%PDF-1.4\n garbage", "application/pdf")}, data={"mode": "viva"})
    assert r.status_code == 422
    r = client.post("/api/upload", files={"file": ("../../etc/passwd.pdf", (SAMPLES / "crowdsense_viva_demo.pdf").read_bytes(), "application/pdf")},
                    data={"mode": "pitch"})
    assert r.status_code == 200 and "/" not in r.json()["filename"]
    r = client.post("/api/upload", files={"file": ("x.pdf", b"", "application/pdf")}, data={"mode": "viva"})
    assert r.status_code == 400
    r = client.post("/api/upload", files={"file": ("a.pdf", b"%PDF", "application/pdf")}, data={"mode": "karaoke"})
    assert r.status_code == 400
    assert client.get("/api/sessions/not-a-session").status_code == 404
    for probe in ("/api/sessions/../../etc/passwd", "/..%2f..%2f..%2fetc%2fpasswd", "/%2e%2e/%2e%2e/etc/passwd"):
        assert "root:" not in client.get(probe).text
    assert client.get("/api/sessions/s_0000000000/slides/1.png").status_code == 404


def test_ws_unknown_session(client):
    with client.websocket_connect("/ws/s_deadbeef00") as ws:
        assert ws.receive_json()["type"] == "fatal"


def test_image_only_and_empty_slides(client, tmp_path):
    import pymupdf
    llm_mod.set_llm(LLMProvider("offline", "", supports_vision=False))
    doc = pymupdf.open()
    doc.new_page()  # empty
    p = doc.new_page()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 400, 300), False)
    pix.set_rect(pix.irect, (120, 90, 200))
    p.insert_image(pymupdf.Rect(50, 50, 500, 400), pixmap=pix)
    data = doc.tobytes()
    r = client.post("/api/upload", files={"file": ("img.pdf", data, "application/pdf")}, data={"mode": "presentation"})
    assert r.status_code == 200
    slides = r.json()["slides"]
    assert slides[0]["is_empty"] is True
    assert slides[1]["visual_elements"][0]["kind"] == "image"
    sid = r.json()["id"]
    with client.websocket_connect(f"/ws/{sid}") as ws:
        ws.receive_json()
        ws.send_json({"type": "start"})
        ws.send_json({"type": "end"})  # no speech at all
        read_until(ws, lambda m: m["type"] == "report_ready")
    rep = client.get(f"/api/sessions/{sid}/report").json()["report"]
    assert rep["scores"][0]["value"] is None and "Unable to determine" in rep["scores"][0]["derivation"][0]

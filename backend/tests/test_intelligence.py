"""Technical communication, claims, presentation memory, weakest slide, rehearsal, extended report (additive)."""
import pytest
from fastapi.testclient import TestClient

from app.agents import intelligence_report as ir
from app.agents import llm as llm_mod
from app.agents.llm import LLMProvider
from app.analysis.claims import detect_claims
from app.analysis.memory import find_inconsistencies, statements_from
from app.analysis.tech_comm import analyze_terms, heuristic_audience
from app.main import app
from app.models import SlideContext
from tests.test_integration import FakeLLM, read_until


def test_density_is_transparent_and_explainability():
    r = analyze_terms([
        "We use LangGraph to orchestrate our multimodal agents and YOLOv8 for object detection over a WebSocket API.",
        "LangGraph coordinates our AI agents, which means it controls how information flows between them.",
        "The rest of the talk is about our team and our plans for the next few months with mentors and friends.",
    ])
    assert r["density_pct"] == round(r["technical_occurrences"] / r["meaningful_words"] * 100, 1)
    t = {x["key"]: x for x in r["terms"]}
    assert t["langgraph"]["explained_heuristic"] == "yes"
    assert t["yolov8"]["explained_heuristic"] == "no" and t["yolov8"]["term"] == "YOLOv8"
    assert analyze_terms(["hello there"])["density_pct"] is None  # too little speech: not measured


def test_audience_heuristic_depends_on_mode():
    assert heuristic_audience("interview", 30, 0.2)["level"] == "appropriate"
    assert heuristic_audience("pitch", 30, 0.6)["level"] == "likely_difficult"
    assert heuristic_audience("presentation", None, 0)["level"] is None


def test_claims():
    sl = [SlideContext(slide_number=1, title="Results", text=["YOLOv8s improves mAP by 13% over YOLOv5s"])]
    cs = detect_claims([(1, "Our model improves accuracy by 25 percent over YOLOv5."), (1, "YOLOv8s improves mAP by 13% over YOLOv5s."),
                        (1, "Our system is highly scalable and users love it."), (1, "We use FastAPI and React for the web app."),
                        (1, "Today I will walk you through the project.")], sl)
    by = {c["claim"][:20]: c for c in cs}
    assert by["Our model improves a"]["type"] == "quantitative" and by["Our model improves a"]["needs_evidence"]
    assert not by["YOLOv8s improves mAP"]["needs_evidence"]
    assert by["Our system is highly"]["type"] == "subjective" and "bottleneck" in by["Our system is highly"]["challenge"]
    assert by["We use FastAPI and R"]["type"] == "technical" and not by["We use FastAPI and R"]["needs_evidence"]
    assert not any(c["claim"].startswith("Today") for c in cs)


def test_memory_potential_inconsistency():
    sl = [SlideContext(slide_number=i, title="x", text=["y"]) for i in (1, 2, 3)]
    st = statements_from(1, "We store all user data in MongoDB.") + statements_from(3, "Our backend stores data in PostgreSQL.")
    inc = find_inconsistencies(st, sl)
    assert len(inc) == 1 and "Potential inconsistency" in inc[0]["message"] and "MongoDB" in inc[0]["message"]
    both = statements_from(1, "We use MongoDB for logs and PostgreSQL for users.") + statements_from(3, "User data lives in PostgreSQL, we use it daily.")
    assert find_inconsistencies(both, sl) == []  # both explicitly used: no flag


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _present(client, mode="viva"):
    sid = client.post("/api/samples/crowdsense_viva_demo.pdf", data={"mode": mode}).json()["id"]
    with client.websocket_connect(f"/ws/{sid}") as ws:
        ws.receive_json()
        ws.send_json({"type": "start"})
        ws.send_json({"type": "slide", "slide_number": 3})
        ws.send_json({"type": "transcript", "seq": 1, "slide_number": 3, "t_start": 0, "t_end": 12,
                      "text": "Our pipeline uses YOLOv8 with DeepSORT and CSRNet. We store every alert in MongoDB so operators can review them later."})
        ws.send_json({"type": "slide", "slide_number": 4})
        ws.send_json({"type": "transcript", "seq": 2, "slide_number": 4, "t_start": 13, "t_end": 25,
                      "text": "um so our YOLOv8 model improves the accuracy by 25 percent over YOLOv5 and our system is highly scalable. "
                              "All detections are saved in PostgreSQL for the dashboard."})
        ws.send_json({"type": "slide", "slide_number": 5})
        fb, _ = read_until(ws, lambda m: m["type"] == "feedback" and m["item"]["category"] == "cross_slide_consistency")
        ws.send_json({"type": "end"})
        read_until(ws, lambda m: m["type"] == "report_ready")
    return sid, fb, client.get(f"/api/sessions/{sid}/report").json()["report"]


def test_live_memory_and_extended_report_offline(client):
    llm_mod.set_llm(LLMProvider("offline", "", supports_vision=False))
    sid, fb, rep = _present(client)
    assert "MongoDB" in fb["item"]["observation"] and "PostgreSQL" in fb["item"]["observation"]
    intel = rep["intelligence"]
    tc = intel["technical_communication"]
    assert tc["density_pct"] is not None and tc["terms_detected"] > 0 and tc["audience"]["source"] == "rule"
    assert any(c["needs_evidence"] for c in intel["claims"]["items"])
    assert intel["consistency_memory"]
    assert intel["weakest_slide"]["slide_number"] == 4
    assert any(r["reason"] == "slide/speech mismatch" for r in intel["weakest_slide"]["reasons"])
    # existing report sections are unchanged and present
    for k in ("overview", "delivery", "consistency", "visuals", "questions", "scores", "narrative"):
        assert k in rep
    assert sid


class TechLLM(FakeLLM):
    async def generate_json(self, system, prompt, images=None, max_tokens=1200, timeout=None, temperature=0.3):
        if system.startswith("You assess technical communication"):
            return {"terms": [{"term": "YOLOv8", "explained": "no", "simplification": "an object detector that finds people in each frame"},
                              {"term": "Kubernetes", "explained": "no", "simplification": "invented"}],
                    "audience": {"level": "appropriate", "rationale": "Viva examiners expect this vocabulary."},
                    "claims": [{"claim": "our system is highly scalable", "type": "subjective", "needs_evidence": True,
                                "challenge": "Which component limits how scalable the system is?"}]}
        return await super().generate_json(system, prompt, images, max_tokens, timeout, temperature)


def test_extended_report_ai_is_validated(client):
    llm_mod.set_llm(TechLLM())
    _, _, rep = _present(client)
    tc = rep["intelligence"]["technical_communication"]
    terms = {t["term"]: t for t in tc["key_terms"]}
    assert terms["YOLOv8"]["explained_source"] == "ai" and terms["YOLOv8"]["simplification"]
    assert "Kubernetes" not in terms  # the model cannot add terms the presenter never said
    assert tc["audience"]["level"] == "appropriate" and tc["audience"]["source"] == "ai"


def test_report_survives_intelligence_failure(client, monkeypatch):
    llm_mod.set_llm(LLMProvider("offline", "", supports_vision=False))

    async def boom(session):
        raise RuntimeError("boom")
    monkeypatch.setattr(ir, "build_intelligence", boom)
    _, _, rep = _present(client)
    assert "error" in rep["intelligence"] and rep["scores"]


def test_rehearsal_flow(client):
    llm_mod.set_llm(LLMProvider("offline", "", supports_vision=False))
    sid, _, rep = _present(client)
    live_sid = client.post("/api/samples/crowdsense_viva_demo.pdf", data={"mode": "viva"}).json()["id"]
    assert client.post(f"/api/sessions/{live_sid}/rehearse", data={"slide_number": 4}).status_code == 409
    assert client.post(f"/api/sessions/{sid}/rehearse", data={"slide_number": 99}).status_code == 404
    r = client.post(f"/api/sessions/{sid}/rehearse", data={"slide_number": 4})
    assert r.status_code == 200
    child = r.json()
    assert child["current_slide"] == 4 and child["rehearsal"]["parent_id"] == sid and child["status"] == "ready"
    assert client.get(f"/api/sessions/{child['id']}/slides/4.png").status_code == 200
    with client.websocket_connect(f"/ws/{child['id']}") as ws:
        ws.receive_json()
        ws.send_json({"type": "start"})
        ws.send_json({"type": "activity", "slide_number": 4, "voiced_s": 20})
        ws.send_json({"type": "transcript", "seq": 1, "slide_number": 4, "t_start": 0, "t_end": 20,
                      "text": "As the bar chart shows, YOLOv8s improves mAP by 13 percent over YOLOv5s. It runs at 38 FPS versus 9 FPS "
                              "for Faster R-CNN, and we trained it on 4,200 annotated frames from dense crowds."})
        ws.send_json({"type": "end"})
        read_until(ws, lambda m: m["type"] == "report_ready")
    reh = client.get(f"/api/sessions/{child['id']}/report").json()["report"]["intelligence"]["rehearsal"]
    rows = {c["metric"]: c for c in reh["changes"]}
    assert rows["content_issues"]["after"] < rows["content_issues"]["before"] and rows["content_issues"]["improved"]
    assert rows["key_point_coverage"]["after"] >= rows["key_point_coverage"]["before"]

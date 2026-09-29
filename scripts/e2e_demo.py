"""Browser end to end test of the hackathon demo flow.

Chromium gets a fake microphone, and the Web Speech API is replaced by a scripted recognizer so the REAL
frontend speech code path runs deterministically. Screenshots are written to ./e2e_shots/.

Run with the server up (python -m uvicorn app.main:app --port 8000 from backend/):
    python scripts/e2e_demo.py [base_url]
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
OUT = Path("e2e_shots")
OUT.mkdir(exist_ok=True)

MOCK_SPEECH = """
(() => {
  class FakeRec {
    constructor() { this.lang = 'en-US'; this.continuous = true; this.interimResults = true; window.__rec = this; }
    start() { this.running = true; }
    stop() { this.running = false; this.onend && this.onend(); }
    abort() { this.running = false; }
  }
  window.webkitSpeechRecognition = FakeRec;
  window.SpeechRecognition = FakeRec;
  window.__say = (text) => {
    const r = window.__rec;
    if (!r || !r.running || !r.onresult) return false;
    const mk = (final) => ({ resultIndex: 0, results: { length: 1, 0: { isFinal: final, length: 1, 0: { transcript: text } } } });
    r.onresult(mk(false));
    r.onresult(mk(true));
    return true;
  };
})();
"""


def say(page, text):
    for _ in range(20):
        if page.evaluate("t => window.__say(t)", text):
            return
        time.sleep(0.2)
    raise RuntimeError("recognizer not running")


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"])
        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, permissions=["microphone"])
        ctx.add_init_script(MOCK_SPEECH)
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: m.type == "error" and errors.append(m.text))

        page.goto(BASE)
        expect(page.get_by_text("Present. Get understood. Get better.")).to_be_visible()
        page.get_by_role("radio", name="Viva").click()
        page.screenshot(path=str(OUT / "01_home.png"))

        # upload the sample through the real file input
        page.set_input_files("input[type=file]", str(Path(__file__).resolve().parent.parent / "samples" / "crowdsense_viva_demo.pdf"))
        expect(page.get_by_role("button", name="Start presenting")).to_be_enabled(timeout=20000)
        page.screenshot(path=str(OUT / "02_ready.png"))
        page.get_by_role("button", name="Start presenting").click()
        expect(page.locator(".status-live")).to_be_visible(timeout=10000)

        say(page, "Good morning everyone. Today I'm presenting CrowdSense, a real time crowd risk detection system that uses CCTV footage, YOLOv8 and density estimation.")
        page.get_by_role("button", name="Next slide").click()
        say(page, "Um so the problem is that crowd crushes are really dangerous and uh operators cannot watch every camera at the same time, so we need automation.")
        page.get_by_role("button", name="Next slide").click()
        page.get_by_role("button", name="Next slide").click()
        # slide 4: deliberately misquote the number and ignore the chart
        say(page, "So on this slide our YOLOv8 model improves the accuracy by 25 percent over YOLOv5 and it is really fast. We trained it on a lot of frames and it works well for dense crowds in practice.")
        time.sleep(1)
        page.get_by_role("button", name="Next slide").click()

        expect(page.locator(".fb-warning").first).to_be_visible(timeout=15000)
        expect(page.locator(".card-q .q-text").first).to_be_visible(timeout=15000)
        page.screenshot(path=str(OUT / "03_feedback_and_question.png"))
        question = page.locator(".card-q .q-text").first.inner_text()
        print("QUESTION:", question)

        page.get_by_role("button", name="Answer", exact=True).first.click()
        say(page, "Because the 13 percent is the gain in mAP measured on our dense crowd test split of 600 images, and we chose YOLOv8 since it runs at 38 FPS.")
        page.get_by_role("button", name="Done answering").click()
        expect(page.locator(".card-q.answered")).to_be_visible(timeout=15000)
        expect(page.get_by_text("Follow-up 1")).to_be_visible(timeout=15000)
        page.screenshot(path=str(OUT / "04_followup.png"))
        print("FOLLOW-UP:", page.locator(".card-q:not(.answered) .q-text").first.inner_text())

        page.get_by_role("button", name="Answer", exact=True).first.click()
        say(page, "The test split was sampled from different cameras than training to avoid leakage.")
        page.get_by_role("button", name="Done answering").click()
        expect(page.locator(".card-q.answered")).to_be_visible(timeout=15000)

        # refresh mid presentation must restore the session
        page.reload()
        expect(page.locator(".status-live")).to_be_visible(timeout=10000)
        expect(page.locator(".fb-warning").first).to_be_visible(timeout=10000)

        page.get_by_role("button", name="End presentation").click()
        expect(page.get_by_text("Top improvements")).to_be_visible(timeout=30000)
        page.screenshot(path=str(OUT / "05_report.png"), full_page=True)

        page.get_by_role("button", name="Practice again").click()
        expect(page.get_by_role("button", name="Start presenting")).to_be_visible(timeout=10000)
        browser.close()
        real_errors = [e for e in errors if "favicon" not in e and "ERR_TUNNEL" not in e]
        if real_errors:
            print("BROWSER ERRORS:", real_errors)
            sys.exit(1)
        print("E2E OK")


if __name__ == "__main__":
    main()

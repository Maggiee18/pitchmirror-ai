"""Browser test for the intelligence layer: answer analysis, Try Again, extended report, weakest slide rehearsal.

Run with the server up:  python scripts/e2e_intelligence.py [base_url]
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_demo import MOCK_SPEECH, OUT, say  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"])
        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, permissions=["microphone"])
        ctx.add_init_script(MOCK_SPEECH)
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(BASE)
        page.get_by_role("radio", name="Viva").click()
        page.get_by_text("Try the sample viva deck").click()
        expect(page.get_by_role("button", name="Start presenting")).to_be_enabled(timeout=20000)
        page.locator(".check-label input").first.uncheck()  # camera off for speed
        page.get_by_role("button", name="Start presenting").click()
        expect(page.locator(".status-live")).to_be_visible(timeout=10000)

        page.get_by_role("button", name="Next slide").click()
        page.get_by_role("button", name="Next slide").click()
        say(page, "Our pipeline uses YOLOv8 with DeepSORT and CSRNet, and we store every alert in MongoDB so operators can review them.")
        page.get_by_role("button", name="Next slide").click()
        say(page, "So our YOLOv8 model improves the accuracy by 25 percent over YOLOv5 and our system is highly scalable. "
                  "All detections are saved in PostgreSQL for the dashboard.")
        page.get_by_role("button", name="Next slide").click()
        expect(page.get_by_text("Earlier vs now").first).to_be_visible(timeout=15000)
        expect(page.locator(".card-q .q-text").first).to_be_visible(timeout=15000)

        page.get_by_role("button", name="Answer", exact=True).first.click()
        say(page, "it is faster")
        page.get_by_role("button", name="Done answering").click()
        expect(page.locator(".ai-badge").first).to_be_visible(timeout=15000)
        page.screenshot(path=str(OUT / "10_answer_intel.png"))

        page.get_by_role("button", name="Try again").click()
        say(page, "Because the 13 percent is the mAP gain of YOLOv8s over YOLOv5s on our dense crowd test split, "
                  "and we chose it since it runs at 38 FPS compared with 9 FPS for Faster R-CNN.")
        page.get_by_role("button", name="Done answering").click()
        expect(page.get_by_text("Attempt 2").first).to_be_visible(timeout=15000)
        page.screenshot(path=str(OUT / "11_try_again.png"))
        print("RETRY:", page.locator(".ai-compare, .ai-panel .muted.small").first.inner_text()[:160])

        page.get_by_role("button", name="End presentation").click()
        expect(page.get_by_text("Technical communication")).to_be_visible(timeout=40000)
        expect(page.get_by_text("Q&A intelligence")).to_be_visible()
        expect(page.get_by_text("Claims & consistency")).to_be_visible()
        expect(page.get_by_text("Top improvements")).to_be_visible()  # existing report still there
        page.screenshot(path=str(OUT / "12_report_intel.png"), full_page=True)

        rehearse = page.get_by_role("button", name="Rehearse slide")
        expect(rehearse).to_be_visible()
        print("WEAKEST:", rehearse.inner_text())
        rehearse.click()
        expect(page.get_by_text("Rehearsal of slide")).to_be_visible(timeout=15000)
        page.locator(".check-label input").first.uncheck()
        page.get_by_role("button", name="Start presenting").click()
        expect(page.locator(".status-live")).to_be_visible(timeout=10000)
        say(page, "As the bar chart shows, YOLOv8s improves mAP by 13 percent over YOLOv5s. It runs at 38 FPS versus 9 FPS "
                  "for Faster R-CNN, and we trained it on 4,200 annotated frames of dense crowds.")
        time.sleep(0.5)
        page.get_by_role("button", name="End presentation").click()
        expect(page.get_by_text("Rehearsal: slide")).to_be_visible(timeout=40000)
        page.screenshot(path=str(OUT / "13_rehearsal.png"), full_page=True)
        page.get_by_role("button", name="Back to the full report").click()
        expect(page.get_by_text("Q&A intelligence")).to_be_visible(timeout=15000)
        browser.close()
        if errors:
            print("PAGE ERRORS:", errors)
            sys.exit(1)
        print("INTELLIGENCE E2E OK")


if __name__ == "__main__":
    main()

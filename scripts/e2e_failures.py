"""Browser failure-mode checks: blocked microphone, typing fallback, oversized deck, invalid file, server restart.

Run with the server up:  python scripts/e2e_failures.py [base_url]
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pymupdf
from playwright.sync_api import expect, sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
RESTART_CMD = sys.argv[2] if len(sys.argv) > 2 else None  # optional shell command that restarts the server
SAMPLE = Path(__file__).resolve().parent.parent / "samples" / "crowdsense_viva_demo.pdf"


def main():
    tmp = Path(tempfile.mkdtemp())
    big = tmp / "big.pdf"
    d = pymupdf.open()
    for i in range(75):
        d.new_page().insert_text((72, 72), f"Slide {i}")
    d.save(str(big))
    bad = tmp / "notes.pdf"
    bad.write_bytes(b"this is not a pdf")

    with sync_playwright() as p:
        browser = p.chromium.launch()  # no fake media flags: microphone is unavailable/denied
        page = browser.new_context(viewport={"width": 1280, "height": 860}).new_page()
        page.goto(BASE)

        page.set_input_files("input[type=file]", str(bad))
        expect(page.locator(".error-box")).to_contain_text("valid PDF", timeout=10000)
        print("invalid file: OK")

        page.set_input_files("input[type=file]", str(big))
        expect(page.locator(".error-box")).to_contain_text("limit is", timeout=20000)
        print("too many slides: OK")

        page.set_input_files("input[type=file]", str(SAMPLE))
        expect(page.get_by_role("button", name="Start presenting")).to_be_enabled(timeout=20000)
        page.select_option(".engine-row select", "browser") if page.locator("option[value=browser]:not([disabled])").count() else None
        page.get_by_role("button", name="Start presenting").click()
        expect(page.locator(".start-card .error-box")).to_be_visible(timeout=10000)
        print("mic unavailable message: OK")

        page.get_by_role("button", name="Continue by typing").click()
        page.get_by_role("button", name="Start presenting").click()
        expect(page.locator(".status-live")).to_be_visible(timeout=10000)
        box = page.get_by_label("Type transcript text")
        box.fill("CrowdSense detects crowd risk from CCTV using YOLOv8 and density estimation to raise early alerts.")
        box.press("Enter")
        expect(page.locator(".t-seg.typed")).to_be_visible(timeout=5000)
        page.get_by_role("button", name="Ask me a question").click()
        expect(page.locator(".card-q .q-text").first).to_be_visible(timeout=15000)
        print("typing fallback + on-demand question: OK")

        if RESTART_CMD:
            subprocess.run(RESTART_CMD, shell=True, check=True)
            time.sleep(4)
            expect(page.get_by_text("Session unavailable")).to_be_visible(timeout=30000)
            page.get_by_role("button", name="Upload a deck").click()
            expect(page.get_by_text("Present. Get understood. Get better.")).to_be_visible()
            print("server restart -> graceful recovery: OK")
        browser.close()
    print("FAILURE CHECKS OK")


if __name__ == "__main__":
    main()

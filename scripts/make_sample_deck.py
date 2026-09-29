"""Generates the demo decks in samples/ (a PDF for the main demo and a small PPTX for format testing).

Run: python scripts/make_sample_deck.py
"""
from __future__ import annotations

import io
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pymupdf  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "samples"
W, H = 960, 540
INK = (0.08, 0.1, 0.18)
MUTED = (0.35, 0.38, 0.48)
ACCENT = (0.36, 0.32, 0.86)


def title(page, text, sub=None):
    page.insert_text((48, 72), text, fontsize=28, fontname="hebo", color=INK)
    if sub:
        page.insert_text((48, 100), sub, fontsize=14, fontname="helv", color=MUTED)


def bullets(page, items, x=56, y=140, size=17, width=820):
    for it in items:
        rect = pymupdf.Rect(x + 18, y - size, x + width, y + size * 3)
        page.draw_circle((x + 5, y - size * 0.3), 3.2, color=ACCENT, fill=ACCENT)
        used = page.insert_textbox(rect, it, fontsize=size, fontname="helv", color=INK)
        lines = 1 if used >= size else 2
        y += int(size * 1.9 * lines) + 6
    return y


def chart_png() -> bytes:
    fig, ax = plt.subplots(figsize=(6.4, 3.6), dpi=150)
    models = ["YOLOv5s", "Faster R-CNN", "YOLOv8s (ours)"]
    vals = [0.71, 0.79, 0.84]
    bars = ax.bar(models, vals, color=["#b8b5e8", "#b8b5e8", "#5b52db"])
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.01, f"{v:.2f}", ha="center", fontsize=11)
    ax.set_ylim(0.5, 0.9)
    ax.set_ylabel("mAP@0.5")
    ax.set_title("Person detection mAP@0.5 on dense crowd test set")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()


def box(page, rect, label, fill=(0.93, 0.92, 1.0)):
    page.draw_rect(rect, color=ACCENT, fill=fill, width=1.4, radius=0.15)
    page.insert_textbox(pymupdf.Rect(rect.x0 + 4, rect.y0 + rect.height / 2 - 16, rect.x1 - 4, rect.y1),
                        label, fontsize=12, fontname="helv", color=INK, align=1)


def arrow(page, p1, p2):
    page.draw_line(p1, p2, color=MUTED, width=1.6)
    page.draw_polyline([(p2[0] - 7, p2[1] - 5), p2, (p2[0] - 7, p2[1] + 5)], color=MUTED, width=1.6)


def make_pdf() -> Path:
    doc = pymupdf.open()

    p = doc.new_page(width=W, height=H)
    p.insert_text((48, 230), "CrowdSense", fontsize=48, fontname="hebo", color=INK)
    p.insert_text((48, 275), "Real-time crowd risk detection from CCTV using YOLOv8 and density estimation",
                  fontsize=17, fontname="helv", color=MUTED)
    p.insert_text((48, 330), "Final year project viva  |  Department of CSE", fontsize=14, fontname="helv", color=ACCENT)

    p = doc.new_page(width=W, height=H)
    title(p, "Problem")
    bullets(p, [
        "Crowd crushes caused more than 1,400 deaths worldwide between 2010 and 2023",
        "Manual CCTV monitoring: one operator watches around 40 camera feeds",
        "Existing systems count people but do not predict dangerous density build up",
        "Goal: raise an alert at least 60 seconds before density crosses 5 people per square metre",
    ])

    p = doc.new_page(width=W, height=H)
    title(p, "System Architecture", "End to end pipeline running at 38 FPS on an RTX 3060")
    y0, h = 230, 70
    xs = [40, 215, 390, 565, 740]
    labels = ["CCTV stream", "YOLOv8s person detector", "DeepSORT tracker", "CSRNet density map", "Risk scorer + alerts"]
    for x, lab in zip(xs, labels):
        box(p, pymupdf.Rect(x, y0, x + 150, y0 + h), lab)
    for a, b in zip(xs[:-1], xs[1:]):
        arrow(p, (a + 152, y0 + h / 2), (b - 2, y0 + h / 2))
    box(p, pymupdf.Rect(390, 380, 540, 440), "Optical flow (Farneback)", fill=(0.95, 0.95, 0.97))
    arrow(p, (465, 380), (465, y0 + h + 2))
    p.insert_text((48, 490), "Risk score = density x flow turbulence, smoothed over a 10 s window",
                  fontsize=13, fontname="helv", color=MUTED)

    p = doc.new_page(width=W, height=H)
    title(p, "Detection Results")
    p.insert_image(pymupdf.Rect(40, 110, 560, 402), stream=chart_png())
    bullets(p, [
        "YOLOv8s improves mAP by 13% over YOLOv5s",
        "Runs at 38 FPS vs 9 FPS for Faster R-CNN",
        "Trained on 4,200 annotated frames",
    ], x=580, y=160, size=15, width=360)
    p.insert_text((48, 440), "Figure: detection accuracy comparison on our dense crowd test split",
                  fontsize=12, fontname="helv", color=MUTED)

    p = doc.new_page(width=W, height=H)
    title(p, "Density Estimation Evaluation", "Mean Absolute Error (lower is better)")
    rows = [["Dataset", "MCNN", "CSRNet (ours)", "Improvement"],
            ["ShanghaiTech A", "110.2", "68.2", "38%"],
            ["ShanghaiTech B", "26.4", "10.6", "60%"],
            ["UCF-QNRF", "277.0", "135.4", "51%"]]
    x0, y0, cw, rh = 80, 150, 200, 48
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            rect = pymupdf.Rect(x0 + c * cw, y0 + r * rh, x0 + (c + 1) * cw, y0 + (r + 1) * rh)
            p.draw_rect(rect, color=MUTED, width=0.8, fill=(0.93, 0.92, 1.0) if r == 0 else None)
            p.insert_textbox(pymupdf.Rect(rect.x0 + 10, rect.y0 + 15, rect.x1 - 6, rect.y1), cell, fontsize=14,
                             fontname="hebo" if r == 0 else "helv", color=INK)
    p.insert_text((80, 380), "Risk alerts: 92% precision, 87% recall on 36 annotated incident clips",
                  fontsize=15, fontname="helv", color=INK)

    p = doc.new_page(width=W, height=H)
    title(p, "Limitations and Future Work")
    bullets(p, [
        "Night time footage reduces detection recall to 0.61",
        "Heavy occlusion above 8 people per square metre breaks tracking",
        "Future: thermal cameras, edge deployment on Jetson Orin",
        "Future: evaluate on live festival footage with the city police control room",
    ])

    OUT.mkdir(exist_ok=True)
    out = OUT / "crowdsense_viva_demo.pdf"
    doc.save(str(out))
    return out


def make_pptx() -> Path:
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.util import Inches

    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    s = prs.slides.add_slide(prs.slide_layouts[5])
    s.shapes.title.text = "Monthly Active Users"
    data = CategoryChartData()
    data.categories = ["Jan", "Feb", "Mar", "Apr", "May", "Jun"]
    data.add_series("MAU (thousands)", (12, 15, 19, 26, 31, 42))
    s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(0.8), Inches(1.6), Inches(8), Inches(5), data)
    tb = s.shapes.add_textbox(Inches(9.2), Inches(2), Inches(3.8), Inches(3)).text_frame
    tb.text = "Revenue increased 42% after introducing feature X"
    s.notes_slide.notes_text_frame.text = "Explain the April jump: referral program launch."
    s2 = prs.slides.add_slide(prs.slide_layouts[1])
    s2.shapes.title.text = "Why now"
    s2.placeholders[1].text_frame.text = "Teams waste 6 hours a week preparing presentations"
    out = OUT / "growth_pitch_demo.pptx"
    prs.save(str(out))
    return out


if __name__ == "__main__":
    print(make_pdf())
    print(make_pptx())

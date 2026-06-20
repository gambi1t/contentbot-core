"""Render a styled HTML file to a presentable PDF (+ full-page PNG) via Chromium.

Usage: python tools/render_html_pdf.py <input.html> <output.pdf> [output.png]
Uses Playwright Chromium (same engine that renders carousels) — real fonts,
gradients, emoji, page CSS. Far better looking than reportlab.
"""
from __future__ import annotations

import sys
from pathlib import Path
from playwright.sync_api import sync_playwright


def render(html_path: Path, pdf_path: Path, png_path: Path | None) -> None:
    url = html_path.resolve().as_uri()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 900, "height": 1273}, device_scale_factor=2)
        page.goto(url, wait_until="networkidle")
        page.emulate_media(media="print")
        page.pdf(
            path=str(pdf_path),
            format="A4",
            print_background=True,
            prefer_css_page_size=True,
            margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
        )
        if png_path:
            page.emulate_media(media="screen")
            page.screenshot(path=str(png_path), full_page=True)
        browser.close()


if __name__ == "__main__":
    inp = Path(sys.argv[1])
    out_pdf = Path(sys.argv[2])
    out_png = Path(sys.argv[3]) if len(sys.argv) > 3 else None
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    render(inp, out_pdf, out_png)
    print(f"PDF: {out_pdf} ({out_pdf.stat().st_size/1024:.1f} KB)")
    if out_png:
        print(f"PNG: {out_png} ({out_png.stat().st_size/1024:.1f} KB)")

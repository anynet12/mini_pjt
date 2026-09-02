"""SVG -> PNG 래스터화 (하이브리드/네이버 이미지용). Playwright Chromium 사용.

사용법:
    python scripts/svg_to_png.py <images_디렉토리> [--scale 2]

지정한 폴더 안의 *.svg 파일을 전부 읽어 같은 이름의 .png로 변환해 같은 폴더에 저장한다.
SVG의 viewBox를 읽어 원본 비율 그대로, scale배(기본 2배) 해상도로 렌더링한다.
"""

import argparse
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

VIEWBOX_RE = re.compile(r'viewBox="\s*[\d.\-]+\s+[\d.\-]+\s+([\d.]+)\s+([\d.]+)\s*"')


def parse_viewbox(svg_text: str) -> tuple[float, float]:
    m = VIEWBOX_RE.search(svg_text)
    if not m:
        raise ValueError("SVG에 viewBox가 없어 렌더링 크기를 알 수 없습니다.")
    return float(m.group(1)), float(m.group(2))


def build_html(svg_text: str, width: float, height: float) -> str:
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<style>html,body{margin:0;padding:0;}"
        f"svg{{display:block;width:{width}px;height:{height}px;}}"
        "</style></head><body>" + svg_text + "</body></html>"
    )


def convert_all(images_dir: Path, scale: float) -> list[Path]:
    svg_files = sorted(images_dir.glob("*.svg"))
    if not svg_files:
        return []

    png_paths = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            for svg_path in svg_files:
                svg_text = svg_path.read_text(encoding="utf-8")
                width, height = parse_viewbox(svg_text)
                html = build_html(svg_text, width, height)

                page = browser.new_page(
                    viewport={"width": round(width), "height": round(height)},
                    device_scale_factor=scale,
                )
                page.set_content(html, wait_until="networkidle")
                png_path = svg_path.with_suffix(".png")
                page.screenshot(path=str(png_path))
                page.close()

                png_paths.append(png_path)
                print(f"변환 완료: {svg_path.name} -> {png_path.name}")
        finally:
            browser.close()

    return png_paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images_dir", help="SVG가 들어있는 폴더 (예: posts/2026-08-25/a4/images_v1)")
    parser.add_argument("--scale", type=float, default=2.0, help="해상도 배율 (기본 2배)")
    args = parser.parse_args()

    images_dir = Path(args.images_dir)
    if not images_dir.is_dir():
        print(f"폴더를 찾을 수 없습니다: {images_dir}", file=sys.stderr)
        sys.exit(1)

    png_paths = convert_all(images_dir, args.scale)
    if not png_paths:
        print(f"SVG 파일이 없습니다: {images_dir}")


if __name__ == "__main__":
    main()

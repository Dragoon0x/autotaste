"""
screenshot.py — Render designs to screenshots.

Uses Playwright to capture what users would actually see.
Two viewports: desktop and mobile. The scoring model judges these,
not the code.
"""

import asyncio
import sys
from pathlib import Path

VIEWPORTS = [
    {"width": 1440, "height": 900, "name": "desktop"},
    {"width": 375, "height": 812, "name": "mobile"},
]

SETTLE_MS = 1500


async def take_screenshots(html_path: Path, output_dir: Path) -> list[Path]:
    from playwright.async_api import async_playwright

    html_path = html_path.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    screenshots = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        for vp in VIEWPORTS:
            ctx = await browser.new_context(
                viewport={"width": vp["width"], "height": vp["height"]},
                device_scale_factor=2,
            )
            page = await ctx.new_page()
            await page.goto(f"file://{html_path}")
            await page.wait_for_timeout(SETTLE_MS)
            out = output_dir / f"{vp['name']}.png"
            await page.screenshot(path=str(out), full_page=True, type="png")
            screenshots.append(out)
            await ctx.close()
            print(f"    {vp['name']} ({vp['width']}x{vp['height']}) ✓")
        await browser.close()

    return screenshots


def screenshot_sync(html_path: Path, output_dir: Path) -> list[Path]:
    return asyncio.run(take_screenshots(html_path, output_dir))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: uv run screenshot.py <path_to_html> [output_dir]")
        sys.exit(1)

    html_path = Path(sys.argv[1])
    if not html_path.exists():
        print(f"ERROR: {html_path} not found.")
        sys.exit(1)

    output_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else html_path.parent
    print(f"screenshotting {html_path}...")
    paths = screenshot_sync(html_path, output_dir)
    print(f"\n{len(paths)} screenshots saved to {output_dir}/")

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from playwright.async_api import Page

from rappi.io_utils import iso_now


DOM_CANDIDATE_SELECTORS: list[tuple[str, str]] = [
    ("subtotal_breakdown", '[data-testid="subtotal-breakdown"]'),
    ("cart", '[data-test="cart"]'),
    ("go_to_checkout_button", '[data-testid="go-to-checkout-button"]'),
    ("main", "main"),
    ("dialog", '[role="dialog"]'),
    ("section", "section"),
    ("aside", "aside"),
]


async def dump_checkout_debug_artifacts(
    page: Page,
    collector: Any,
    output_dir: Path,
    logger: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    log = logger or (lambda _: None)
    output_dir.mkdir(parents=True, exist_ok=True)

    page_url_file = output_dir / "page_url.txt"
    page_title_file = output_dir / "page_title.txt"
    page_content_file = output_dir / "page_content.html"
    body_text_file = output_dir / "body_text.txt"
    screenshot_file = output_dir / "checkout_screenshot.png"
    network_index_file = output_dir / "network_index.json"
    selector_index_file = output_dir / "selector_index.json"
    payloads_file = output_dir / "checkout_payloads.json"
    debug_index_file = output_dir / "debug_index.json"

    page_url_file.write_text(page.url, encoding="utf-8")
    page_title_file.write_text(await page.title(), encoding="utf-8")
    page_content_file.write_text(await page.content(), encoding="utf-8")
    body_text = ""
    try:
        body_text = await page.inner_text("body", timeout=3000)
    except Exception:  # noqa: BLE001
        body_text = ""
    body_text_file.write_text(body_text, encoding="utf-8")
    await page.screenshot(path=str(screenshot_file), full_page=True)

    selector_summary: list[dict[str, Any]] = []
    for selector_name, selector in DOM_CANDIDATE_SELECTORS:
        loc = page.locator(selector)
        try:
            count = await loc.count()
        except Exception:  # noqa: BLE001
            count = 0
        item_record: dict[str, Any] = {
            "selector_name": selector_name,
            "selector": selector,
            "match_count": count,
            "saved_matches": [],
        }
        for idx in range(count):
            node = loc.nth(idx)
            try:
                visible = await node.is_visible(timeout=250)
            except Exception:  # noqa: BLE001
                visible = False
            try:
                text = await node.inner_text(timeout=1200)
            except Exception:  # noqa: BLE001
                text = ""
            try:
                html = await node.inner_html(timeout=1200)
            except Exception:  # noqa: BLE001
                html = ""

            if not visible and not (text.strip() or html.strip()):
                continue
            safe_name = _safe_name(selector_name)
            base = f"{safe_name}_{idx:03d}"
            html_file = output_dir / f"{base}.html"
            text_file = output_dir / f"{base}.txt"
            html_file.write_text(html, encoding="utf-8")
            text_file.write_text(text, encoding="utf-8")
            item_record["saved_matches"].append(
                {
                    "index": idx,
                    "visible": visible,
                    "html_file": html_file.name,
                    "text_file": text_file.name,
                }
            )
        selector_summary.append(item_record)

    selector_index_file.write_text(json.dumps(selector_summary, ensure_ascii=False, indent=2), encoding="utf-8")

    payloads_file.write_text(
        json.dumps(
            {
                "ts": iso_now(),
                "payload_steps": getattr(collector, "payload_steps", {}),
                "payloads": getattr(collector, "payloads", {}),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    network_rows: list[dict[str, Any]] = []
    for idx, item in enumerate(getattr(collector, "checkout_candidate_bodies", []), start=1):
        body_format = item.get("body_format", "txt")
        ext = "json" if body_format == "json" else "txt"
        body_filename = f"resp_{idx:03d}.{ext}"
        body_path = output_dir / body_filename
        body_content = item.get("body")
        if body_content is None:
            body_content = ""
        body_path.write_text(body_content, encoding="utf-8")
        network_rows.append(
            {
                "index": idx,
                "url": item.get("url"),
                "method": item.get("method"),
                "status": item.get("status"),
                "content_type": item.get("content_type"),
                "reason": item.get("reason"),
                "body_file": body_filename,
            }
        )
    network_index_file.write_text(json.dumps(network_rows, ensure_ascii=False, indent=2), encoding="utf-8")

    debug_index = {
        "ts": iso_now(),
        "output_dir": str(output_dir),
        "page_url_file": page_url_file.name,
        "page_title_file": page_title_file.name,
        "page_content_file": page_content_file.name,
        "body_text_file": body_text_file.name,
        "checkout_screenshot_file": screenshot_file.name,
        "selector_index_file": selector_index_file.name,
        "network_index_file": network_index_file.name,
        "checkout_payloads_file": payloads_file.name,
    }
    debug_index_file.write_text(json.dumps(debug_index, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"[DEBUG] checkout_debug_dump: saved artifacts at {output_dir}")
    return {
        "debug_dir": str(output_dir),
        "debug_index_file": str(debug_index_file),
        "screenshot_path": str(screenshot_file),
        "network_index_file": str(network_index_file),
        "selector_index_file": str(selector_index_file),
        "checkout_payload_file": str(payloads_file),
    }


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", value).strip("_").lower()
    return cleaned or "selector"

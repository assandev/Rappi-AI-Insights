from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from playwright.async_api import Page


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def truncate(text: str, max_chars: int) -> str:
    return text if len(text) <= max_chars else text[:max_chars] + "... [trimmed]"


def write_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


def write_json_array(path: Path, payload: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


async def save_screenshot(
    page: Page,
    output_path: Path,
    logger: Callable[[str], None] | None = None,
) -> Path | None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        await page.screenshot(path=str(output_path), full_page=True)
        if logger:
            logger(f"[INFO] Screenshot saved: {output_path}")
        return output_path
    except Exception as exc:  # noqa: BLE001 - scraper must remain resilient
        if logger:
            logger(f"[WARN] Could not save screenshot ({type(exc).__name__}): {exc}")
        return None


def step_log(logger: Callable[[str], None], step_no: int, message: str) -> None:
    logger("====================================")
    logger(f"[STEP {step_no}] {message}")

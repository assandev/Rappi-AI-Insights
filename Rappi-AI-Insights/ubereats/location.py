from __future__ import annotations

import re
import time
import unicodedata
from typing import Callable

from playwright.async_api import Page

from ubereats.errors import CheckoutStepError
from ubereats.readiness import wait_for_home_ready, wait_for_location_manager_ready
from ubereats.ui_actions import safe_click, safe_fill, safe_select_first_result, wait_ui_not_busy


async def set_address_if_needed(
    page: Page,
    address: str,
    selectors: dict[str, list[str]],
    timeout_ms: int,
    logger: Callable[[str], None],
) -> None:
    await wait_for_home_ready(page, timeout_ms=min(timeout_ms, 12000), logger=logger)
    current = await _read_current_location_text(page, selectors)
    if _location_matches_target(current, address):
        logger("[DEBUG] location_skip_same: current location already matches target")
        return

    await safe_click(
        page,
        selectors["edit_location"][0],
        timeout_ms=min(timeout_ms, 6000),
        logger=logger,
        step_name="edit_location",
        ready_selector=selectors["global_search_input"][0],
    )
    await wait_for_location_manager_ready(page, timeout_ms=min(timeout_ms, 6000), logger=logger)
    await safe_fill(
        page,
        selectors["location_input"][0],
        value=address,
        timeout_ms=min(timeout_ms, 7000),
        logger=logger,
        step_name="location_input",
    )
    await safe_select_first_result(
        page,
        selectors["location_suggestion"],
        timeout_ms=min(timeout_ms, 7000),
        logger=logger,
        step_name="location_suggestion",
    )

    skip_selector = await _first_visible_selector(
        page,
        selectors["location_skip"],
        timeout_ms=min(timeout_ms, 3500),
    )
    if skip_selector:
        await safe_click(
            page,
            skip_selector,
            timeout_ms=min(timeout_ms, 2500),
            logger=logger,
            step_name="location_skip",
        )
        logger(f"[DEBUG] location_skip: clicked optional selector {skip_selector}")
        await _wait_any_visible_selector(
            page,
            selectors["location_save"],
            timeout_ms=min(timeout_ms, 4000),
        )
    else:
        logger("[DEBUG] location_skip: not shown, continuing to save")

    save_selector = await _first_visible_selector(
        page,
        selectors["location_save"],
        timeout_ms=min(timeout_ms, 5000),
    )
    if not save_selector:
        raise CheckoutStepError(f"Location save button not visible. Tried={selectors['location_save']}")

    await safe_click(
        page,
        save_selector,
        timeout_ms=min(timeout_ms, 5000),
        logger=logger,
        step_name="location_save",
    )
    await page.wait_for_function(
        "() => !document.querySelector('input[data-testid=\"location-typeahead-input\"]')",
        timeout=min(timeout_ms, 8000),
    )
    await wait_for_home_ready(page, timeout_ms=min(timeout_ms, 12000), logger=logger)


async def _read_current_location_text(page: Page, selectors: dict[str, list[str]]) -> str | None:
    probe_selectors: list[str] = [
        *selectors.get("edit_location", []),
        '[data-testid="delivery-location-label"]',
        '[data-testid*="location" i]',
        '[aria-label*="ubicacion" i]',
        '[aria-label*="location" i]',
    ]
    seen: set[str] = set()
    for selector in probe_selectors:
        if selector in seen:
            continue
        seen.add(selector)
        try:
            loc = page.locator(selector)
            count = await loc.count()
            for idx in range(min(count, 3)):
                node = loc.nth(idx)
                if not await node.is_visible(timeout=180):
                    continue
                text = (await node.inner_text(timeout=350) or "").strip()
                if text:
                    return text
        except Exception:
            continue
    return None


def _location_matches_target(current_location: str | None, target_address: str) -> bool:
    if not current_location or not target_address:
        return False
    current_norm = _normalize_location(current_location)
    target_norm = _normalize_location(target_address)
    if not current_norm or not target_norm:
        return False

    target_head = _normalize_location(target_address.split(",")[0])
    if target_head and (target_head in current_norm or current_norm in target_head):
        return True

    current_head = _normalize_location(current_location.split("•")[0].split(",")[0])
    if current_head and (current_head in target_norm or target_norm in current_head):
        return True

    stopwords = {"av", "avenida", "calle", "c", "de", "del", "la", "el"}
    target_tokens = [t for t in target_head.split() if len(t) > 2 and t not in stopwords]
    if not target_tokens:
        return False
    hits = sum(1 for token in target_tokens if token in current_norm)
    return hits >= min(2, len(target_tokens))


def _normalize_location(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value))
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^a-z0-9\s]", " ", ascii_only.lower())
    return " ".join(cleaned.split())


async def _first_visible_selector(
    page: Page,
    selectors: list[str],
    timeout_ms: int,
) -> str | None:
    deadline = time.monotonic() + (timeout_ms / 1000.0)
    while time.monotonic() < deadline:
        for selector in selectors:
            try:
                if await page.locator(selector).first.is_visible(timeout=180):
                    return selector
            except Exception:
                continue
        await wait_ui_not_busy(page, timeout_ms=250)
    return None


async def _wait_any_visible_selector(
    page: Page,
    selectors: list[str],
    timeout_ms: int,
) -> None:
    selector = await _first_visible_selector(page, selectors, timeout_ms=timeout_ms)
    if selector is None:
        raise CheckoutStepError(f"No visible selector found. Tried={selectors}")

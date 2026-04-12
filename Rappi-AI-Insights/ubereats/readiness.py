from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Callable

from playwright.async_api import Page

from ubereats.errors import CheckoutStepError
from ubereats.ui_actions import trial_clickable, wait_ui_not_busy


def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


def _deadline(timeout_ms: int) -> float:
    return _now_ts() + (timeout_ms / 1000.0)


def _normalize_text(value: str) -> str:
    folded = "".join(
        ch
        for ch in unicodedata.normalize("NFKD", str(value))
        if not unicodedata.combining(ch)
    )
    cleaned = re.sub(r"[^a-z0-9\s]", " ", folded.lower())
    return " ".join(cleaned.split())


async def wait_for_home_ready(
    page: Page,
    timeout_ms: int,
    logger: Callable[[str], None],
    selector_override: str | None = None,
) -> None:
    search_selector = selector_override or 'input[data-testid="search-input"]'
    deadline = _deadline(timeout_ms)
    stable = 0
    while _now_ts() < deadline:
        url = page.url.lower()
        in_feed_like = "/feed" in url or "/mx" in url
        try:
            search_visible = await page.locator(search_selector).first.is_visible(timeout=180)
        except Exception:
            search_visible = False
        cart_clickable = await trial_clickable(page, 'button[data-test-id="view-carts-btn"]', timeout_ms=350)
        await wait_ui_not_busy(page, timeout_ms=450)
        if in_feed_like and search_visible and cart_clickable:
            stable += 1
            if stable >= 2:
                logger("[DEBUG] wait_for_home_ready: ready")
                return
        else:
            stable = 0
        await wait_ui_not_busy(page, timeout_ms=280)
    raise CheckoutStepError("wait_for_home_ready: timed out")


async def wait_for_cart_ready(page: Page, timeout_ms: int, logger: Callable[[str], None]) -> None:
    await page.wait_for_selector('div[data-test="cart"]', state="visible", timeout=timeout_ms)
    await wait_ui_not_busy(page, timeout_ms=min(timeout_ms, 1200))
    logger("[DEBUG] wait_for_cart_ready: cart drawer visible")


async def wait_for_location_manager_ready(page: Page, timeout_ms: int, logger: Callable[[str], None]) -> None:
    await page.wait_for_selector('input[data-testid="location-typeahead-input"]', state="visible", timeout=timeout_ms)
    logger("[DEBUG] wait_for_location_manager_ready: input visible")


async def wait_for_search_results_ready(page: Page, timeout_ms: int, logger: Callable[[str], None]) -> None:
    deadline = _deadline(timeout_ms)
    while _now_ts() < deadline:
        url_ok = "/search" in page.url.lower()
        has_result = False
        for selector in ("text=/Resultado superior/i", "main a[href*='/store/']", "main h3"):
            try:
                if await page.locator(selector).first.is_visible(timeout=120):
                    has_result = True
                    break
            except Exception:
                continue
        if url_ok and has_result:
            logger("[DEBUG] wait_for_search_results_ready: ready")
            return
        await wait_ui_not_busy(page, timeout_ms=320)
    raise CheckoutStepError("wait_for_search_results_ready: timed out")


async def wait_for_restaurant_ready(page: Page, timeout_ms: int, logger: Callable[[str], None]) -> None:
    deadline = _deadline(timeout_ms)
    while _now_ts() < deadline:
        url = page.url.lower()
        if "/store/" in url or "/tienda/" in url:
            for selector in ('input[placeholder*="Buscar en"]', 'input[data-testid="search-input"]'):
                try:
                    if await page.locator(selector).first.is_visible(timeout=140):
                        logger("[DEBUG] wait_for_restaurant_ready: store ready")
                        return
                except Exception:
                    continue
        await wait_ui_not_busy(page, timeout_ms=320)
    raise CheckoutStepError("wait_for_restaurant_ready: timed out")


async def wait_for_checkout_ready(
    page: Page,
    selectors: dict[str, list[str]],
    collector: Any | None,
    timeout_ms: int,
    logger: Callable[[str], None],
) -> None:
    marker_selectors = selectors.get("checkout_marker", [])
    required_any = {"cart", "checkout", "summary", "pricing", "order", "checkout_presentation"}
    deadline = _deadline(timeout_ms)
    while _now_ts() < deadline:
        url_ok = "/checkout" in page.url.lower()
        marker_ok = False
        for selector in marker_selectors:
            try:
                if await page.locator(selector).first.is_visible(timeout=120):
                    marker_ok = True
                    break
            except Exception:
                continue
        payload_ok = True
        if collector is not None:
            fresh = {k for k in collector.payloads.keys() if collector.payload_steps.get(k, 0) >= 9}
            payload_ok = bool(fresh & required_any)
        if (url_ok or marker_ok) and payload_ok:
            logger("[DEBUG] wait_for_checkout_ready: checkout UI and payload ready")
            return
        await wait_ui_not_busy(page, timeout_ms=320)
    raise CheckoutStepError("wait_for_checkout_ready: timed out")


async def wait_for_store_search_results_active(
    page: Page,
    expected_product: str,
    preferred_input_selector: str | None,
    timeout_ms: int,
    logger: Callable[[str], None],
) -> None:
    expected_norm = _normalize_text(expected_product)
    observed_inputs: list[str] = []
    probe_inputs: list[str] = []
    if preferred_input_selector:
        probe_inputs.append(preferred_input_selector)
    probe_inputs.extend(['input[placeholder*="Buscar en"]', 'input[data-testid="search-input"]'])

    for selector in probe_inputs:
        try:
            loc = page.locator(selector).first
            if not await loc.is_visible(timeout=180):
                continue
            current_raw = (await loc.input_value(timeout=250) or "").strip()
            current_norm = _normalize_text(current_raw)
            if current_norm:
                observed_inputs.append(current_norm)
        except Exception:
            continue

    if observed_inputs:
        logger(f"[DEBUG] store_search_input_observed={observed_inputs}")
    if expected_norm and observed_inputs and expected_norm not in observed_inputs[0]:
        logger("[DEBUG] store_search_input_hint: not exact; relying on product cards visibility")
    if not observed_inputs:
        logger("[DEBUG] store_search_input_hint: no visible input probe; relying on result signals")

    deadline = _deadline(timeout_ms)
    while _now_ts() < deadline:
        try:
            cards = page.locator(
                "main h4, "
                "main button[data-testid='quick-add-button'], "
                "main button[aria-label*='Agregar' i], "
                "main button[aria-label*='Add' i]"
            )
            count = await cards.count()
            if count > 0:
                logger(f"[DEBUG] store_search_state_ready: product cards visible (count={count})")
                return
        except Exception:
            pass
        try:
            await page.wait_for_function(
                "() => !document.querySelector('[role=\"progressbar\"]') && !document.querySelector('[aria-busy=\"true\"]')",
                timeout=350,
            )
        except Exception:
            pass

    raise CheckoutStepError(
        f"Store search state not ready: no product cards visible after in-store search. observed_inputs={observed_inputs}"
    )

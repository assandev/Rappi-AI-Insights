from __future__ import annotations

import re
from typing import Callable

from playwright.async_api import Page

from ubereats.readiness import wait_for_cart_ready, wait_for_home_ready
from ubereats.ui_actions import safe_click


async def clear_cart_if_needed(
    page: Page,
    selectors: dict[str, list[str]],
    timeout_ms: int,
    logger: Callable[[str], None],
) -> None:
    await wait_for_home_ready(page, timeout_ms=min(timeout_ms, 12000), logger=logger)
    badge = await page.query_selector(selectors["cart_badge"][0])
    has_items = False
    if badge:
        text = (await badge.inner_text() or "").strip()
        has_items = text not in {"", "0"}
        logger(f"[DEBUG] cart_has_items: {has_items} (badge='{text}')")
    else:
        logger("[DEBUG] cart_has_items: False (badge missing)")
    if not has_items:
        logger("[DEBUG] cleanup_skipped_empty: cart appears empty")
        return

    await safe_click(
        page,
        selectors["cart_open"][0],
        timeout_ms=min(timeout_ms, 8000),
        logger=logger,
        step_name="clear_cart_open",
        ready_selector=selectors["global_search_input"][0],
        allow_force=True,
    )
    await wait_for_cart_ready(page, timeout_ms=min(timeout_ms, 7000), logger=logger)

    removed = 0
    while True:
        before_count = await page.locator(selectors["cart_remove"][0]).count()
        btn = page.locator(selectors["cart_remove"][0]).first
        if not await btn.is_visible(timeout=220):
            break
        await safe_click(
            page,
            btn,
            timeout_ms=min(timeout_ms, 3200),
            logger=logger,
            step_name="clear_cart_remove_click",
            max_attempts=5,
        )
        removed += 1
        try:
            await page.wait_for_function(
                """
                ([selector, beforeCount]) => {
                    const now = document.querySelectorAll(selector).length;
                    return now < beforeCount;
                }
                """,
                [selectors["cart_remove"][0], before_count],
                timeout=min(timeout_ms, 2200),
            )
        except Exception:
            pass

    logger(f"[DEBUG] remove_click_count: {removed}")
    await page.wait_for_function(
        "() => !document.querySelector('div[data-test=\"cart\"] button[data-test=\"item-stepper-dec\"]')",
        timeout=min(timeout_ms, 8000),
    )
    logger("[DEBUG] cart_empty_confirmed: no decrement buttons visible")


async def read_cart_badge_count(page: Page, badge_selector: str) -> int | None:
    try:
        badge = page.locator(badge_selector).first
        if not await badge.is_visible(timeout=250):
            return None
        text = (await badge.inner_text(timeout=350) or "").strip()
        m = re.search(r"\d+", text)
        return int(m.group(0)) if m else None
    except Exception:
        return None


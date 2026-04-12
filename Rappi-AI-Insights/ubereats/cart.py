from __future__ import annotations

import re
from typing import Callable

from playwright.async_api import Locator, Page

from ubereats.errors import CheckoutStepError
from ubereats.readiness import wait_for_cart_ready, wait_for_home_fully_ready
from ubereats.ui_actions import safe_click, wait_ui_not_busy


async def clear_cart_if_needed(
    page: Page,
    selectors: dict[str, list[str]],
    timeout_ms: int,
    logger: Callable[[str], None],
) -> None:
    await wait_for_home_fully_ready(page, timeout_ms=min(timeout_ms, 15000), logger=logger)
    initial_badge_count = await read_cart_badge_count(page, selectors["cart_badge"][0])
    has_items = initial_badge_count is not None and initial_badge_count > 0
    logger(f"[DEBUG] cart_has_items: {has_items} (badge='{initial_badge_count}')")
    logger(f"[DEBUG] clear_cart_badge_before={initial_badge_count}")
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
    await wait_for_cart_ready(
        page,
        timeout_ms=min(timeout_ms, 12000),
        logger=logger,
        selectors=selectors,
        require_cart_root=True,
    )

    remove_click_count = 0
    while True:
        cart_root = await _get_visible_cart_root(page, selectors)
        if cart_root is None:
            break
        remove_candidates = await _find_remove_candidates(cart_root, logger=logger)
        logger(f"[DEBUG] clear_cart_remove_candidates={len(remove_candidates)}")
        if not remove_candidates:
            break

        remove_btn = remove_candidates[0]
        _, meta = await _is_valid_remove_button(remove_btn)
        logger(f"[DEBUG] clear_cart_remove_button_found={meta}")
        badge_before_click = await read_cart_badge_count(page, selectors["cart_badge"][0])

        await safe_click(
            page,
            remove_btn,
            timeout_ms=min(timeout_ms, 4000),
            logger=logger,
            step_name="clear_cart_remove_click",
            max_attempts=5,
        )
        remove_click_count += 1

        await _wait_for_remove_state_change(
            page,
            selectors=selectors,
            previous_badge_count=badge_before_click,
            timeout_ms=min(timeout_ms, 5000),
        )

    logger(f"[DEBUG] clear_cart_remove_click_count={remove_click_count}")
    badge_after = await read_cart_badge_count(page, selectors["cart_badge"][0])
    logger(f"[DEBUG] clear_cart_badge_after={badge_after}")

    if (initial_badge_count or 0) > 0 and remove_click_count == 0:
        raise CheckoutStepError("Cart had items but no valid remove button was clicked.")

    visual_empty = await verify_cart_visually_empty(
        page=page,
        selectors=selectors,
        timeout_ms=min(timeout_ms, 10000),
        logger=logger,
    )
    if not visual_empty:
        raise CheckoutStepError("Cart is not visually empty after clear-cart flow.")

    await _close_cart_drawer_if_open(
        page=page,
        selectors=selectors,
        timeout_ms=min(timeout_ms, 8000),
        logger=logger,
    )
    await wait_for_home_fully_ready(page, timeout_ms=min(timeout_ms, 12000), logger=logger)


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


async def verify_cart_visually_empty(
    page: Page,
    selectors: dict[str, list[str]],
    timeout_ms: int,
    logger: Callable[[str], None],
) -> bool:
    deadline = _deadline(timeout_ms)
    last_state: dict[str, object] | None = None

    while _now_ts() < deadline:
        cart_root = await _get_visible_cart_root(page, selectors)
        drawer_visible = cart_root is not None
        remove_buttons = await _count_remove_buttons_in_root(cart_root, logger=logger) if cart_root else 0
        items_visible = await _count_visible_cart_items(cart_root) if cart_root else 0
        empty_state = await _has_empty_state(cart_root) if cart_root else False
        badge_now = await read_cart_badge_count(page, selectors["cart_badge"][0])
        badge_zero = badge_now in (None, 0)

        state = {
            "empty_state": empty_state,
            "badge_zero": badge_zero,
            "remove_buttons": remove_buttons,
            "items_visible": items_visible,
            "drawer_visible": drawer_visible,
            "badge_now": badge_now,
        }
        if state != last_state:
            logger(
                "[DEBUG] clear_cart_visual_verify: "
                f"empty_state={empty_state} "
                f"badge_zero={badge_zero} "
                f"remove_buttons={remove_buttons} "
                f"items_visible={items_visible} "
                f"drawer_visible={drawer_visible}"
            )
            last_state = state

        if drawer_visible:
            if remove_buttons == 0 and (empty_state or items_visible == 0) and badge_zero:
                return True
        else:
            if badge_zero:
                return True

        await wait_ui_not_busy(page, timeout_ms=300)

    return False


async def _close_cart_drawer_if_open(
    page: Page,
    selectors: dict[str, list[str]],
    timeout_ms: int,
    logger: Callable[[str], None],
) -> None:
    cart_root = await _get_visible_cart_root(page, selectors)
    if cart_root is None:
        return

    close_candidates = [
        'button[aria-label*="Cerrar" i]',
        'button[aria-label*="Close" i]',
        'button:has-text("Cerrar")',
        'button:has-text("Close")',
    ]
    close_selector = await _first_visible_selector(page, close_candidates, timeout_ms=1200)
    if close_selector:
        await safe_click(
            page,
            close_selector,
            timeout_ms=min(timeout_ms, 3000),
            logger=logger,
            step_name="clear_cart_close_drawer",
            max_attempts=3,
        )
    else:
        await safe_click(
            page,
            selectors["cart_open"][0],
            timeout_ms=min(timeout_ms, 4000),
            logger=logger,
            step_name="clear_cart_toggle_drawer",
            allow_force=True,
            max_attempts=4,
        )

    await _wait_for_cart_root_hidden(page, selectors, timeout_ms=min(timeout_ms, 5000))
    if await _get_visible_cart_root(page, selectors) is not None:
        raise CheckoutStepError("Cart drawer remained open after clear-cart completion.")


async def _wait_for_remove_state_change(
    page: Page,
    selectors: dict[str, list[str]],
    previous_badge_count: int | None,
    timeout_ms: int,
) -> None:
    deadline = _deadline(timeout_ms)
    previous_remove_count = await _count_global_remove_buttons(page)
    while _now_ts() < deadline:
        current_badge = await read_cart_badge_count(page, selectors["cart_badge"][0])
        current_remove_count = await _count_global_remove_buttons(page)
        if _badge_has_changed(previous_badge_count, current_badge):
            return
        if current_remove_count < previous_remove_count:
            return
        await wait_ui_not_busy(page, timeout_ms=250)


def _badge_has_changed(previous: int | None, current: int | None) -> bool:
    if previous is None:
        return current in (0, None)
    if current is None:
        return True
    return current < previous


async def _find_remove_candidates(cart_root: Locator, logger: Callable[[str], None]) -> list[Locator]:
    candidates: list[Locator] = []
    buttons = cart_root.locator("button")
    total = min(await buttons.count(), 120)
    for idx in range(total):
        btn = buttons.nth(idx)
        try:
            if not await btn.is_visible(timeout=120):
                continue
            is_valid, meta = await _is_valid_remove_button(btn)
            if is_valid:
                candidates.append(btn)
            elif "data-test=more-options" in meta:
                logger("[DEBUG] clear_cart_ignored_button=data-test=more-options")
        except Exception:
            continue
    return candidates


async def _is_valid_remove_button(button: Locator) -> tuple[bool, str]:
    data_test = ((await button.get_attribute("data-test")) or "").strip()
    aria_label = ((await button.get_attribute("aria-label")) or "").strip()
    data_test_lower = data_test.lower()
    aria_lower = aria_label.lower()

    if data_test_lower == "more-options":
        return False, "data-test=more-options"

    has_trash_icon = False
    try:
        titles = button.locator("svg title")
        count = min(await titles.count(), 4)
        for idx in range(count):
            title_text = (await titles.nth(idx).inner_text(timeout=120) or "").strip().lower()
            if "trash can" in title_text:
                has_trash_icon = True
                break
    except Exception:
        has_trash_icon = False

    valid = (
        data_test_lower == "item-stepper-dec"
        or "reducci" in aria_lower
        or has_trash_icon
    )
    meta = f"data-test={data_test or '<none>'} aria-label={aria_label or '<none>'}"
    return valid, meta


async def _count_remove_buttons_in_root(cart_root: Locator, logger: Callable[[str], None]) -> int:
    count = 0
    buttons = cart_root.locator("button")
    total = min(await buttons.count(), 120)
    for idx in range(total):
        btn = buttons.nth(idx)
        try:
            if not await btn.is_visible(timeout=100):
                continue
            is_valid, meta = await _is_valid_remove_button(btn)
            if is_valid:
                count += 1
            elif "data-test=more-options" in meta:
                logger("[DEBUG] clear_cart_ignored_button=data-test=more-options")
        except Exception:
            continue
    return count


async def _count_visible_cart_items(cart_root: Locator) -> int:
    selectors = [
        '[data-testid*="cart-item" i]',
        '[data-test*="cart-item" i]',
        '[data-test*="line-item" i]',
        'li:has(button[data-test="item-stepper-dec"])',
        'div:has(button[data-test="item-stepper-dec"])',
    ]
    for selector in selectors:
        items = cart_root.locator(selector)
        total = min(await items.count(), 120)
        visible = 0
        for idx in range(total):
            try:
                if await items.nth(idx).is_visible(timeout=90):
                    visible += 1
            except Exception:
                continue
        if visible > 0:
            return visible
    return 0


async def _has_empty_state(cart_root: Locator) -> bool:
    empty_selectors = [
        "text=/carrito vaci|carrito vacío|tu carrito esta vacio|tu carrito está vacío/i",
        "text=/your cart is empty|empty cart/i",
    ]
    for selector in empty_selectors:
        try:
            if await cart_root.locator(selector).first.is_visible(timeout=120):
                return True
        except Exception:
            continue
    return False


async def _count_global_remove_buttons(page: Page) -> int:
    root = page.locator('div[data-test="cart"]')
    if not await root.first.is_visible(timeout=120):
        return 0
    remove = root.first.locator('button[data-test="item-stepper-dec"]')
    total = min(await remove.count(), 120)
    visible = 0
    for idx in range(total):
        try:
            if await remove.nth(idx).is_visible(timeout=90):
                visible += 1
        except Exception:
            continue
    return visible


async def _get_visible_cart_root(page: Page, selectors: dict[str, list[str]]) -> Locator | None:
    for selector in selectors.get("cart_root", ['div[data-test="cart"]']):
        root = page.locator(selector).first
        try:
            if await root.is_visible(timeout=150):
                return root
        except Exception:
            continue
    return None


async def _wait_for_cart_root_hidden(page: Page, selectors: dict[str, list[str]], timeout_ms: int) -> None:
    deadline = _deadline(timeout_ms)
    while _now_ts() < deadline:
        if await _get_visible_cart_root(page, selectors) is None:
            return
        await wait_ui_not_busy(page, timeout_ms=250)


async def _first_visible_selector(page: Page, selectors: list[str], timeout_ms: int) -> str | None:
    deadline = _deadline(timeout_ms)
    while _now_ts() < deadline:
        for selector in selectors:
            try:
                if await page.locator(selector).first.is_visible(timeout=120):
                    return selector
            except Exception:
                continue
        await wait_ui_not_busy(page, timeout_ms=180)
    return None


def _now_ts() -> float:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).timestamp()


def _deadline(timeout_ms: int) -> float:
    return _now_ts() + (timeout_ms / 1000.0)


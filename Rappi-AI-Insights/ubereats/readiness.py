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


async def _is_any_visible(page: Page, selectors: list[str], visible_timeout_ms: int = 140) -> bool:
    for selector in selectors:
        try:
            if await page.locator(selector).first.is_visible(timeout=visible_timeout_ms):
                return True
        except Exception:
            continue
    return False


async def _first_visible_selector(page: Page, selectors: list[str], visible_timeout_ms: int = 140) -> str | None:
    for selector in selectors:
        try:
            if await page.locator(selector).first.is_visible(timeout=visible_timeout_ms):
                return selector
        except Exception:
            continue
    return None


async def _read_badge_count(page: Page, badge_selectors: list[str]) -> int | None:
    for selector in badge_selectors:
        try:
            badge = page.locator(selector).first
            if not await badge.is_visible(timeout=120):
                continue
            text = (await badge.inner_text(timeout=200) or "").strip()
            match = re.search(r"\d+", text)
            if match:
                return int(match.group(0))
        except Exception:
            continue
    return None


async def _has_visible_loader(page: Page) -> bool:
    try:
        return bool(
            await page.evaluate(
                """
                () => {
                  const selectors = [
                    '[role="progressbar"]',
                    '[aria-busy="true"]',
                    '[data-testid*="skeleton" i]',
                    '[class*="skeleton"]'
                  ];
                  const nodes = document.querySelectorAll(selectors.join(','));
                  for (const node of nodes) {
                    const style = window.getComputedStyle(node);
                    if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) {
                      continue;
                    }
                    const rect = node.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {
                      return true;
                    }
                  }
                  return false;
                }
                """
            )
        )
    except Exception:
        return False


async def _collect_visible_titles(page: Page, title_selector: str, limit: int = 16) -> list[str]:
    titles: list[str] = []
    seen: set[str] = set()
    try:
        nodes = page.locator(title_selector)
        probe_count = min(await nodes.count(), 120)
        for idx in range(probe_count):
            node = nodes.nth(idx)
            if not await node.is_visible(timeout=90):
                continue
            raw = (await node.inner_text(timeout=150) or "").strip()
            norm = _normalize_text(raw)
            if not norm:
                continue
            if norm in seen:
                continue
            seen.add(norm)
            titles.append(norm)
            if len(titles) >= limit:
                break
    except Exception:
        return titles
    return titles


async def _count_visible_cards(page: Page, card_selector: str) -> int:
    visible = 0
    try:
        cards = page.locator(card_selector)
        probe_count = min(await cards.count(), 140)
        for idx in range(probe_count):
            if await cards.nth(idx).is_visible(timeout=90):
                visible += 1
    except Exception:
        return visible
    return visible


async def wait_for_home_shell_ready(
    page: Page,
    timeout_ms: int,
    logger: Callable[[str], None],
    selector_override: str | None = None,
) -> None:
    search_selector = selector_override or 'input[data-testid="search-input"]'
    header_markers = [
        'a[data-testid="edit-delivery-location-button"]',
        'header',
    ]
    cart_selector = 'button[data-test-id="view-carts-btn"]'

    deadline = _deadline(timeout_ms)
    stable_checks = 0
    while _now_ts() < deadline:
        try:
            search_visible = await page.locator(search_selector).first.is_visible(timeout=180)
        except Exception:
            search_visible = False
        header_visible = await _is_any_visible(page, header_markers, visible_timeout_ms=120)
        cart_visible = await _is_any_visible(page, [cart_selector], visible_timeout_ms=120)
        cart_clickable = await trial_clickable(page, cart_selector, timeout_ms=350)

        if search_visible and (header_visible or cart_visible) and cart_clickable:
            stable_checks += 1
            if stable_checks >= 2:
                logger("[DEBUG] home_shell_ready: search/header/cart visible")
                return
        else:
            stable_checks = 0
        await wait_ui_not_busy(page, timeout_ms=250)

    raise CheckoutStepError("wait_for_home_shell_ready: timed out")


async def wait_for_home_fully_ready(
    page: Page,
    timeout_ms: int,
    logger: Callable[[str], None],
    selector_override: str | None = None,
) -> None:
    await wait_for_home_shell_ready(
        page,
        timeout_ms=min(timeout_ms, 12000),
        logger=logger,
        selector_override=selector_override,
    )
    logger("[DEBUG] wait_for_home_fully_ready: ready")


async def wait_for_home_ready(
    page: Page,
    timeout_ms: int,
    logger: Callable[[str], None],
    selector_override: str | None = None,
) -> None:
    await wait_for_home_fully_ready(
        page,
        timeout_ms=timeout_ms,
        logger=logger,
        selector_override=selector_override,
    )


async def _cart_loading_skeleton(page: Page, cart_root_selectors: list[str]) -> bool:
    for root_selector in cart_root_selectors:
        if await _is_any_visible(
            page,
            [
                f"{root_selector} [role='progressbar']",
                f"{root_selector} [aria-busy='true']",
                f"{root_selector} [class*='skeleton']",
                f"{root_selector} [data-testid*='skeleton' i]",
            ],
            visible_timeout_ms=120,
        ):
            return True
    return False


async def _cart_real_content(page: Page, cart_root_selectors: list[str], go_checkout_selectors: list[str]) -> bool:
    item_selectors: list[str] = []
    for root_selector in cart_root_selectors:
        item_selectors.extend(
            [
                f"{root_selector} button[data-test='item-stepper-dec']",
                f"{root_selector} button[data-test='more-options']",
                f"{root_selector} [data-testid*='cart-item' i]",
                f"{root_selector} [data-test*='cart-item' i]",
                f"{root_selector} [data-test*='line-item' i]",
                f"{root_selector} li",
            ]
        )
    if await _is_any_visible(page, item_selectors, visible_timeout_ms=120):
        return True
    if await _is_any_visible(page, go_checkout_selectors, visible_timeout_ms=120):
        return True
    return False


async def _cart_empty_state(page: Page, cart_root_selectors: list[str]) -> bool:
    empty_patterns = [
        "text=/carrito vaci|carrito vacío|tu carrito esta vacio|tu carrito está vacío/i",
        "text=/your cart is empty|empty cart/i",
    ]
    scoped: list[str] = []
    for root_selector in cart_root_selectors:
        for pattern in empty_patterns:
            scoped.append(f"{root_selector} {pattern}")
    return await _is_any_visible(page, scoped, visible_timeout_ms=120)


async def wait_for_cart_fully_ready(
    page: Page,
    timeout_ms: int,
    logger: Callable[[str], None],
    selectors: dict[str, list[str]] | None = None,
    previous_badge_count: int | None = None,
    require_cart_root: bool = False,
) -> None:
    cart_root_selectors = selectors.get("cart_root", ['div[data-test="cart"]']) if selectors else ['div[data-test="cart"]']
    go_checkout_selectors = selectors.get("go_checkout", ['a[data-testid="go-to-checkout-button"]']) if selectors else [
        'a[data-testid="go-to-checkout-button"]'
    ]
    continue_selectors = go_checkout_selectors + ['a:has-text("Continuar")', 'button:has-text("Continuar")']
    badge_selectors = selectors.get("cart_badge", ['[data-testid="view-carts-badge"]']) if selectors else [
        '[data-testid="view-carts-badge"]'
    ]

    deadline = _deadline(timeout_ms)
    last_state: dict[str, Any] = {}
    while _now_ts() < deadline:
        cart_root_visible = await _is_any_visible(page, cart_root_selectors)
        cart_loading = await _cart_loading_skeleton(page, cart_root_selectors) if cart_root_visible else False
        cart_real_content = await _cart_real_content(page, cart_root_selectors, go_checkout_selectors) if cart_root_visible else False
        cart_empty_state = await _cart_empty_state(page, cart_root_selectors) if cart_root_visible else False

        go_checkout_visible = await _is_any_visible(page, go_checkout_selectors)
        continue_visible = await _is_any_visible(page, continue_selectors)
        checkout_url = "/checkout" in page.url.lower()
        badge_now = await _read_badge_count(page, badge_selectors)
        badge_increment = False
        if badge_now is not None:
            if previous_badge_count is None:
                badge_increment = badge_now > 0
            else:
                badge_increment = badge_now > previous_badge_count

        state = {
            "cart_root_visible": cart_root_visible,
            "cart_loading_skeleton": cart_loading,
            "cart_real_content": cart_real_content,
            "cart_empty_state": cart_empty_state,
            "go_checkout_visible": go_checkout_visible,
            "continue_visible": continue_visible,
            "checkout_url": checkout_url,
            "badge_now": badge_now,
            "badge_increment": badge_increment,
        }
        if state != last_state:
            logger(f"[DEBUG] cart_root_visible={cart_root_visible}")
            logger(f"[DEBUG] cart_loading_skeleton={cart_loading}")
            logger(f"[DEBUG] cart_real_content={cart_real_content}")
            last_state = state

        drawer_interactable = cart_root_visible and (not cart_loading) and (cart_real_content or cart_empty_state)

        if require_cart_root:
            if drawer_interactable:
                logger("[DEBUG] wait_for_cart_fully_ready: ready")
                return
        else:
            if drawer_interactable or checkout_url or badge_increment or (go_checkout_visible and not cart_loading) or continue_visible:
                logger("[DEBUG] wait_for_cart_fully_ready: ready")
                return

        await wait_ui_not_busy(page, timeout_ms=320)

    raise CheckoutStepError(f"wait_for_cart_fully_ready: timed out. last_state={last_state}")


async def wait_for_cart_ready(
    page: Page,
    timeout_ms: int,
    logger: Callable[[str], None],
    selectors: dict[str, list[str]] | None = None,
    previous_badge_count: int | None = None,
    require_cart_root: bool = False,
) -> None:
    await wait_for_cart_fully_ready(
        page,
        timeout_ms=timeout_ms,
        logger=logger,
        selectors=selectors,
        previous_badge_count=previous_badge_count,
        require_cart_root=require_cart_root,
    )


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


async def wait_for_catalog_stable(
    page: Page,
    card_selector: str,
    title_selector: str,
    stable_window_ms: int,
    timeout_ms: int,
    logger: Callable[[str], None],
) -> list[str]:
    deadline = _deadline(timeout_ms)
    stable_since: float | None = None
    last_signature: tuple[int, tuple[str, ...], int, bool] | None = None
    last_titles: list[str] = []
    last_cards_visible = 0

    while _now_ts() < deadline:
        loaders_visible = await _has_visible_loader(page)
        cards_visible = await _count_visible_cards(page, card_selector)
        titles_visible = await _collect_visible_titles(page, title_selector, limit=20)
        try:
            scroll_height = int(await page.evaluate("() => document.body ? document.body.scrollHeight : 0"))
        except Exception:
            scroll_height = 0

        signature = (cards_visible, tuple(titles_visible[:12]), scroll_height, loaders_visible)
        if signature != last_signature:
            logger(f"[DEBUG] store_search_cards_visible={cards_visible}")
            logger(f"[DEBUG] store_search_titles_visible={titles_visible[:12]}")
            last_signature = signature
            stable_since = None

        if not loaders_visible and cards_visible > 0:
            if stable_since is None:
                stable_since = _now_ts()
            elif ((_now_ts() - stable_since) * 1000.0) >= stable_window_ms:
                logger(f"[DEBUG] store_search_state_stable: stable_window_ms={stable_window_ms}")
                return titles_visible
        else:
            stable_since = None

        last_titles = titles_visible
        last_cards_visible = cards_visible
        await wait_ui_not_busy(page, timeout_ms=300)

    raise CheckoutStepError(
        f"Catalog did not stabilize in time. cards_visible={last_cards_visible} titles_sample={last_titles[:8]}"
    )


async def wait_for_store_search_fully_applied(
    page: Page,
    expected_product: str,
    preferred_input_selector: str | None,
    timeout_ms: int,
    logger: Callable[[str], None],
) -> None:
    expected_norm = _normalize_text(expected_product)
    selector_candidates: list[str] = []
    if preferred_input_selector:
        selector_candidates.append(preferred_input_selector)
    selector_candidates.extend(['input[placeholder*="Buscar en"]', 'input[data-testid="search-input"]'])

    observed_inputs: list[str] = []
    matched_input_selector: str | None = None
    input_deadline = _deadline(min(timeout_ms, 12000))

    while _now_ts() < input_deadline:
        observed_inputs = []
        matched_input_selector = None
        for selector in selector_candidates:
            try:
                loc = page.locator(selector).first
                if not await loc.is_visible(timeout=140):
                    continue
                raw = (await loc.input_value(timeout=220) or "").strip()
                norm = _normalize_text(raw)
                if norm:
                    observed_inputs.append(norm)
                if expected_norm and expected_norm in norm:
                    matched_input_selector = selector
                    break
            except Exception:
                continue

        if observed_inputs:
            logger(f"[DEBUG] store_search_input_observed={observed_inputs}")
        if matched_input_selector:
            logger(f"[DEBUG] store_search_input_used={matched_input_selector}")
            break
        await wait_ui_not_busy(page, timeout_ms=300)

    if not matched_input_selector:
        raise CheckoutStepError(
            f"Store search input value did not stabilize with requested product. observed_inputs={observed_inputs}"
        )

    try:
        await page.locator(matched_input_selector).first.press("Enter", timeout=1500)
        logger("[DEBUG] store_search_submit: pressed Enter for consolidation")
    except Exception:
        logger("[DEBUG] store_search_submit: Enter not applied, continuing with state-based readiness")

    remaining_timeout_ms = max(2000, int((_deadline(timeout_ms) - _now_ts()) * 1000))
    titles = await wait_for_catalog_stable(
        page,
        card_selector=(
            "main [data-qa^='product-item-'], "
            "main button[data-testid='quick-add-button'], "
            "main button[aria-label*='Agregar' i], "
            "main button[aria-label*='Add' i], "
            "main h4"
        ),
        title_selector="main [data-qa^='product-item-'] h4, main h4",
        stable_window_ms=2000,
        timeout_ms=min(remaining_timeout_ms, 15000),
        logger=logger,
    )

    strong_signal = any(expected_norm == t or expected_norm in t for t in titles)
    if strong_signal:
        logger("[DEBUG] store_search_state_confirmed: target_visible_in_titles=True")
        return

    exact_visible = False
    try:
        exact_visible = await page.get_by_text(expected_product, exact=True).first.is_visible(timeout=400)
    except Exception:
        exact_visible = False
    if exact_visible:
        logger("[DEBUG] store_search_state_confirmed: target_visible_exact_text=True")
        return

    raise CheckoutStepError(
        f"Store search stabilized but target product is not visible yet. target='{expected_product}' titles_sample={titles[:12]}"
    )


async def wait_for_store_search_results_active(
    page: Page,
    expected_product: str,
    preferred_input_selector: str | None,
    timeout_ms: int,
    logger: Callable[[str], None],
) -> None:
    await wait_for_store_search_fully_applied(
        page,
        expected_product=expected_product,
        preferred_input_selector=preferred_input_selector,
        timeout_ms=timeout_ms,
        logger=logger,
    )


async def wait_for_product_modal_ready(
    page: Page,
    timeout_ms: int,
    logger: Callable[[str], None],
) -> bool:
    deadline = _deadline(timeout_ms)
    while _now_ts() < deadline:
        dialogs = page.locator("div[role='dialog']")
        try:
            dialog_count = min(await dialogs.count(), 6)
        except Exception:
            dialog_count = 0

        for idx in range(dialog_count):
            dialog = dialogs.nth(idx)
            try:
                if not await dialog.is_visible(timeout=120):
                    continue
                add_btn = dialog.locator("button[data-testid='add-to-cart-button']").first
                if await add_btn.is_visible(timeout=120) and await add_btn.is_enabled(timeout=120):
                    logger("[DEBUG] wait_for_product_modal_ready: modal and add-to-cart button ready")
                    return True
            except Exception:
                continue

        if "/checkout" in page.url.lower():
            logger("[DEBUG] wait_for_product_modal_ready: checkout URL detected, no modal required")
            return False
        if await _is_any_visible(page, ['a[data-testid="go-to-checkout-button"]', 'a:has-text("Continuar")', 'button:has-text("Continuar")']):
            logger("[DEBUG] wait_for_product_modal_ready: checkout CTA visible, no modal required")
            return False

        await wait_ui_not_busy(page, timeout_ms=320)

    logger("[DEBUG] wait_for_product_modal_ready: modal not detected within timeout")
    return False


async def wait_for_cart_state_updated(
    page: Page,
    previous_badge_count: int | None,
    selectors: dict[str, list[str]],
    timeout_ms: int,
    logger: Callable[[str], None],
) -> bool:
    badge_selectors = selectors.get("cart_badge", ['[data-testid="view-carts-badge"]'])
    go_checkout_selectors = selectors.get("go_checkout", ['a[data-testid="go-to-checkout-button"]'])
    cart_root_selectors = selectors.get("cart_root", ['div[data-test="cart"]'])
    continue_selectors = go_checkout_selectors + ['a:has-text("Continuar")', 'button:has-text("Continuar")']

    deadline = _deadline(timeout_ms)
    last_signature: tuple[bool, bool, bool, bool, bool, int | None] | None = None
    while _now_ts() < deadline:
        badge_now = await _read_badge_count(page, badge_selectors)
        badge_increment = False
        if badge_now is not None:
            if previous_badge_count is None:
                badge_increment = badge_now > 0
            else:
                badge_increment = badge_now > previous_badge_count

        go_checkout_visible = await _is_any_visible(page, go_checkout_selectors)
        cart_root_visible = await _is_any_visible(page, cart_root_selectors)
        continue_visible = await _is_any_visible(page, continue_selectors)
        checkout_url = "/checkout" in page.url.lower()
        modal_ready = await _is_any_visible(page, ["div[role='dialog'] button[data-testid='add-to-cart-button']"])

        signature = (modal_ready, badge_increment, go_checkout_visible, cart_root_visible, checkout_url, badge_now)
        if signature != last_signature:
            logger(
                "[DEBUG] post_product_transition: "
                f"modal_ready={modal_ready} "
                f"badge_increment={badge_increment} "
                f"go_checkout={go_checkout_visible} "
                f"cart_root={cart_root_visible} "
                f"checkout_url={checkout_url} "
                f"badge_now={badge_now}"
            )
            last_signature = signature

        if badge_increment or go_checkout_visible or cart_root_visible or continue_visible or checkout_url:
            return True

        await wait_ui_not_busy(page, timeout_ms=320)

    logger("[DEBUG] post_product_transition: timeout without cart/checkout update signal")
    return False

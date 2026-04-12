from __future__ import annotations

from pathlib import Path
from typing import Any, Callable
import unicodedata

from playwright.async_api import Page


class CheckoutStepError(RuntimeError):
    pass


def build_selectors(restaurant: str, product: str) -> dict[str, list[str]]:
    # TODO: validate each selector group on current Rappi UI and keep these lists ranked.
    return {
        "address_open": [
            "div.ButtonAddress__text",
            "div.sc-fXynhf.ButtonAddress__text",
            "header div[class*='ButtonAddress__text']",
            'button:has-text("Direccion")',
            'button:has-text("Entregar en")',
        ],
        "address_input": [
            'input[data-qa="address-input"]',
            'input[placeholder*="direccion de entrega"]',
            'input[placeholder*="Direccion"]',
        ],
        "address_suggestion_button": [
            "button.chakra-button:has(p[data-qa='suggestion-text'])",
            "button.chakra-button.css-1p0d4h7",
        ],
        "address_confirm": [
            'button[data-qa="confirm-address"]',
            "#confirm-address-button",
        ],
        "address_save": [
            "#save-address-button",
            'button[aria-label*="Guardar direccion"]',
            'button[aria-label*="Guardar dirección"]',
        ],
        "current_address_text": [
            'header [class*="address"]',
            "header button:has-text(',')",
            'header [data-testid*="address"]',
        ],
        "restaurant_search_input": [
            'input[placeholder*="Comida"]',
            'input[placeholder*="restaurantes"]',
            'input[placeholder*="productos"]',
            'input[placeholder*="Buscar"]',
            'input[type="search"]',
        ],
        "restaurant_result": [
            f"text=/{restaurant}/i",
            "text=/McDonald/i",
            "a:has-text(\"McDonald's\")",
            "button:has-text(\"McDonald's\")",
        ],
        "product_card": [f"text=/{product}/i"],
        "add_button": [
            'div[role="dialog"] button:has-text("Agregar")',
            'div[role="dialog"] button:has-text("Agregar $")',
            'div[class*="modal"] button:has-text("Agregar")',
            'button:has-text("Agregar al carrito")',
            'button:has-text("Agregar")',
            'button:has-text("Add")',
        ],
        "cart_indicator": [
            'button:has-text("Ver carrito")',
            '[data-testid*="cart"] [class*="badge"]',
            'button[aria-label*="carrito"] [class*="badge"]',
            r"text=/\b1\b/",
        ],
        "cart_icon_only": [
            'button[data-qa="basket-icon"]',
            '[data-qa="basket-icon"]',
            "button:has(svg.shopping-card-icon)",
            '[data-testid*="cart"]',
            'button[aria-label*="carrito"]',
            'a[href*="carrito"]',
        ],
        "cart_icon_fallback": [
            'button[data-qa="basket-icon"]',
            '[data-qa="basket-icon"]',
            "button:has(svg.shopping-card-icon)",
            '[data-testid*="cart"]',
            'button[aria-label*="carrito"]',
            'a[href*="carrito"]',
        ],
        "go_to_pay_button": [
            'button:has-text("Ir al pago")',
            'button:has-text("Ir a pagar")',
            'button:has-text("Pagar")',
            'a:has-text("Ir al pago")',
        ],
        "checkout_address_marker": [
            "text=/Dirección de entrega/i",
            "text=/Direccion de entrega/i",
        ],
        "checkout_summary_marker": [
            "text=/Resumen/i",
            "text=/Total/i",
        ],
        "checkout_place_order_marker": [
            'button:has-text("Hacer pedido")',
            "text=/Hacer pedido/i",
        ],
        "last_craving_modal_close": [
            'button:has-text("Cerrar")',
            'button[aria-label*="Cerrar"]',
            'button[aria-label*="close"]',
            "div[role='dialog'] button:has(svg)",
            "div[role='dialog'] button:has-text('x')",
        ],
    }


async def login_if_needed(
    page: Page,
    context: Any,
    storage_state_path: Path,
    logger: Callable[[str], None],
) -> None:
    try:
        login_visible = await page.get_by_text("Iniciar sesion", exact=False).first.is_visible(timeout=2000)
    except Exception:
        login_visible = False
    if not login_visible:
        return
    logger("[STEP 2] Login required, complete login manually in browser.")
    input("When login is completed, press ENTER to continue...")
    await context.storage_state(path=str(storage_state_path))
    logger(f"[INFO] Updated storage state saved: {storage_state_path}")


async def clear_cart_if_needed(
    page: Page,
    payloads: dict[str, Any],
    timeout_ms: int,
    logger: Callable[[str], None],
) -> None:
    step_start = _now_ts()

    def _log_elapsed(label: str, started_at: float) -> None:
        logger(f"[DEBUG] {label}: {int((_now_ts() - started_at) * 1000)}ms")

    # Aggressive local budgets for clear-cart path (independent from global timeout).
    open_budget_ms = min(1200, timeout_ms)
    substep_budget_ms = min(1400, timeout_ms)

    all_get = payloads.get("all_get")
    has_items = False
    if isinstance(all_get, list) and all_get:
        stores = all_get[0].get("stores") or []
        if stores:
            has_items = True
    if not has_items:
        logger("[DEBUG] Cart already empty.")
        logger(f"[DEBUG] clear_cart_total: {int((_now_ts() - step_start) * 1000)}ms")
        return

    open_started = _now_ts()
    if not await click_cart_icon_strict(page, timeout_ms=timeout_ms, logger=logger):
        await _try_click(
            page,
            ['button:has-text("Ver carrito")'],
            timeout_ms=open_budget_ms,
            group_name="clear_cart_open",
            logger=logger,
        )
    _log_elapsed("clear_cart_open", open_started)

    delete_basket_selectors = ['button[data-qa="delete-basket"]']
    confirm_empty_selectors = [
        "div[role='dialog'] button[data-testid='button'].primary.big",
        "div[class*='modal'] button[data-testid='button'].primary.big",
        "button[data-testid='button'].primary.big",
        'button:has-text("Sí, seguro")', 
        'button:has-text("Confirmar")',
    ]
    begin_shopping_selectors = ['button[data-testid="button"].primary.wide:has-text("Comenzar a comprar")']

    removed_any = False
    # Wait for delete control readiness instead of sleeping.
    await _wait_any_visible(
        page,
        delete_basket_selectors,
        timeout_ms=substep_budget_ms,
        group_name="clear_cart_delete_ready",
        logger=logger,
    )

    delete_started = _now_ts()
    if await _try_click(page, delete_basket_selectors, timeout_ms=substep_budget_ms, group_name="clear_cart_delete_basket", logger=logger):
        removed_any = True
        _log_elapsed("clear_cart_delete_click", delete_started)

        modal = await _first_visible_dialog(page, timeout_ms=substep_budget_ms)
        if modal is None:
            raise CheckoutStepError("Clear-cart confirm modal not visible after delete click.")

        confirm_started = _now_ts()
        confirm_clicked = False
        for selector in [
            'button[data-testid="button"].primary.big:has-text("Sí, seguro")',
            'button[data-testid="button"].primary.big:has-text("Si, seguro")',
            'button[data-testid="button"].primary.big',
        ]:
            try:
                btn = modal.locator(selector).first
                if await btn.is_visible(timeout=220):
                    await btn.click(timeout=substep_budget_ms)
                    logger(f"[DEBUG] clear_cart_confirm: clicked modal scoped {selector}")
                    confirm_clicked = True
                    break
            except Exception:
                continue
        if not confirm_clicked:
            confirm_clicked = await _try_click(
                page,
                ['button[data-testid="button"].primary.big:has-text("Sí, seguro")', 'button[data-testid="button"].primary.big'],
                timeout_ms=900,
                group_name="clear_cart_confirm_fallback",
                logger=logger,
                visible_timeout_ms=250,
            )
        if not confirm_clicked:
            raise CheckoutStepError("Could not click clear-cart confirm button.")
        _log_elapsed("clear_cart_confirm_click", confirm_started)

        close_wait_started = _now_ts()
        try:
            await modal.wait_for(state="hidden", timeout=substep_budget_ms)
            modal_closed = True
        except Exception:
            modal_closed = await _wait_all_hidden(
                page,
                ["section[role='dialog']", "div[role='dialog']", "div[class*='modal']"],
                timeout_ms=700,
            )
        logger(f"[DEBUG] clear_cart_confirm: modal_closed={modal_closed}")
        _log_elapsed("clear_cart_modal_close_wait", close_wait_started)

        begin_started = _now_ts()
        begin_clicked = await _try_click(
            page,
            begin_shopping_selectors,
            timeout_ms=substep_budget_ms,
            group_name="clear_cart_begin_shopping",
            logger=logger,
            visible_timeout_ms=350,
        )
        if not begin_clicked:
            begin_clicked = await _try_click(
                page,
                ['button:has-text("Comenzar a comprar")', "button[data-testid='button'].primary.wide"],
                timeout_ms=800,
                group_name="clear_cart_begin_shopping_fallback",
                logger=logger,
                visible_timeout_ms=250,
            )
        logger(f"[DEBUG] clear_cart_begin_shopping: clicked={begin_clicked}")
        _log_elapsed("clear_cart_begin_shopping", begin_started)
        if begin_clicked:
            await _wait_all_hidden(
                page,
                ["section[role='dialog']", "div[role='dialog']", "div[class*='modal']"],
                timeout_ms=600,
            )
    else:
        remove_selectors = [
            'button[aria-label*="Eliminar"]',
            'button:has(svg[data-testid*="trash"])',
            'button:has(svg[class*="trash"])',
            'button:has-text("Eliminar")',
        ]
        for _ in range(6):
            if await _try_click(
                page,
                remove_selectors,
                timeout_ms=substep_budget_ms,
                group_name="clear_cart_remove_item",
                logger=logger,
            ):
                removed_any = True
                await page.wait_for_timeout(180)
                continue
            break

    if removed_any:
        logger("[DEBUG] Cart cleanup actions executed.")
    else:
        logger("[WARN] Could not find cart-empty controls. TODO verify cart drawer/modal selectors.")
    logger(f"[DEBUG] clear_cart_total: {int((_now_ts() - step_start) * 1000)}ms")


async def set_address_if_needed(
    page: Page,
    address: str,
    selectors: dict[str, list[str]],
    timeout_ms: int,
    logger: Callable[[str], None],
) -> None:
    address_hint = address.split(",")[0].strip()
    header_text = await _try_first_text(page, selectors["current_address_text"], timeout_ms=2500)
    if header_text:
        logger(f"[DEBUG] Current header address text: {header_text}")

    already_set = bool(header_text) and address_hint.lower() in header_text.lower()
    if not already_set:
        try:
            already_set = await page.get_by_text(address_hint, exact=False).first.is_visible(timeout=2000)
        except Exception:
            already_set = False

    if already_set:
        logger(f"[DEBUG] Address already present in header: {address_hint}")
        return

    # Step 4 flow requested:
    # 1) click current address
    # 2) type desired address in address input
    # 3) click unique suggestion chakra button
    # 4) confirm address
    # 5) save address
    await _wait_click(page, selectors["address_open"], timeout_ms, "address_open", logger=logger)
    await page.wait_for_timeout(400)

    await _wait_fill(
        page,
        selectors["address_input"],
        address,
        timeout_ms,
        "address_input",
        logger=logger,
        post_type_wait_ms=1000,
    )
    await page.wait_for_timeout(900)

    await _click_first_address_suggestion(
        page,
        selectors["address_suggestion_button"],
        timeout_ms=timeout_ms,
        logger=logger,
    )
    await page.wait_for_timeout(500)

    await _wait_click(page, selectors["address_confirm"], timeout_ms, "address_confirm", logger=logger)
    await page.wait_for_timeout(500)

    await _wait_click(page, selectors["address_save"], timeout_ms, "address_save", logger=logger)
    await page.wait_for_load_state("networkidle")


async def open_restaurant(
    page: Page,
    restaurant: str,
    selectors: dict[str, list[str]],
    timeout_ms: int,
    logger: Callable[[str], None],
) -> None:
    await _wait_fill(
        page,
        selectors["restaurant_search_input"],
        restaurant,
        timeout_ms,
        "restaurant_search",
        submit=False,
        logger=logger,
        post_type_wait_ms=900,
    )
    await page.wait_for_timeout(1200)
    await _wait_click(page, selectors["restaurant_result"], timeout_ms, "restaurant_result", logger=logger)
    await page.wait_for_load_state("networkidle")


async def add_product(
    page: Page,
    product: str,
    selectors: dict[str, list[str]],
    timeout_ms: int,
    logger: Callable[[str], None],
) -> None:
    # Important: never click a generic "Agregar" before selecting the exact product card.
    # Otherwise, Rappi can add a combo that only partially matches the desired product text.
    await _open_product_modal_exact(page, product=product, timeout_ms=timeout_ms, logger=logger)
    await page.wait_for_timeout(500)
    await _wait_click(page, selectors["add_button"], timeout_ms, "add_button_modal", logger=logger)
    if not await _wait_any_visible(page, selectors["cart_indicator"], timeout_ms=8000, group_name="cart_indicator", logger=logger):
        logger("[DEBUG] Cart indicator not confirmed yet; continuing to cart open attempt.")
    await page.wait_for_load_state("networkidle")


async def go_to_checkout(
    page: Page,
    selectors: dict[str, list[str]],
    timeout_ms: int,
    logger: Callable[[str], None],
) -> None:
    if not await click_cart_icon_strict(page, timeout_ms=timeout_ms, logger=logger):
        try:
            await _wait_click(page, selectors["cart_icon_only"], timeout_ms, "cart_icon_only", logger=logger)
        except CheckoutStepError:
            if not await _try_click(page, selectors["cart_icon_fallback"], timeout_ms, "cart_icon_fallback", logger=logger):
                raise
    await page.wait_for_timeout(600)

    # If we are already in checkout (some flows jump there directly), skip go_to_pay click.
    already_checkout = (
        "/checkout/" in page.url
        or await _wait_any_visible(
            page,
            selectors["checkout_place_order_marker"],
            timeout_ms=1000,
            group_name="checkout_place_order_marker_precheck",
            logger=logger,
        )
    )
    if already_checkout:
        logger("[DEBUG] go_to_checkout: already in checkout, skipping go_to_pay click.")
        return

    # Non-blocking by design: if this button is missing, we continue and rely on network payload readiness.
    clicked = await _try_click(
        page,
        selectors["go_to_pay_button"],
        timeout_ms=1500,
        group_name="go_to_pay_button",
        logger=logger,
        visible_timeout_ms=250,
    )
    if clicked:
        await page.wait_for_load_state("domcontentloaded")
    else:
        logger("[DEBUG] go_to_pay_button not visible/clickable. Continuing with network-based completion.")


async def close_last_craving_modal_if_present(
    page: Page,
    selectors: dict[str, list[str]],
    logger: Callable[[str], None],
) -> None:
    closed = await _try_click(
        page,
        selectors["last_craving_modal_close"],
        timeout_ms=5000,
        group_name="last_craving_modal_close",
        logger=logger,
    )
    if closed:
        logger("[DEBUG] Closed upsell modal before checkout confirmation.")
        await page.wait_for_timeout(700)


async def confirm_checkout_visible(
    page: Page,
    selectors: dict[str, list[str]],
    timeout_ms: int,
    logger: Callable[[str], None],
) -> None:
    await _wait_visible(page, selectors["checkout_address_marker"], timeout_ms, "checkout_address_marker")
    await _wait_visible(page, selectors["checkout_summary_marker"], timeout_ms, "checkout_summary_marker")
    await _wait_visible(page, selectors["checkout_place_order_marker"], timeout_ms, "checkout_place_order_marker")
    await page.wait_for_timeout(3500)
    logger("[DEBUG] Checkout markers visible.")


async def click_cart_icon_strict(page: Page, timeout_ms: int, logger: Callable[[str], None]) -> bool:
    strict_selectors = [
        'button[data-qa="basket-icon"]',
        '[data-qa="basket-icon"]',
        "button:has(svg.shopping-card-icon)",
    ]
    if await _try_click(page, strict_selectors, timeout_ms=timeout_ms, group_name="cart_icon_strict", logger=logger):
        return True
    try:
        clicked = await page.evaluate(
            """
            () => {
              const el = document.querySelector('button[data-qa="basket-icon"]');
              if (!el) return false;
              el.click();
              return true;
            }
            """
        )
        if clicked:
            logger("[DEBUG] cart_icon_strict: clicked via JS fallback")
            return True
    except Exception:
        pass
    return False


async def _wait_click(
    page: Page,
    selectors: list[str],
    timeout_ms: int,
    group_name: str,
    logger: Callable[[str], None],
) -> str:
    last_error: Exception | None = None
    deadline = _deadline(timeout_ms)
    while _now_ts() < deadline:
        for selector in selectors:
            try:
                all_matches = page.locator(selector)
                count = await all_matches.count()
                if count == 0:
                    continue
                for idx in range(count):
                    loc = all_matches.nth(idx)
                    try:
                        if await loc.is_visible(timeout=600) and await loc.is_enabled(timeout=600):
                            await loc.scroll_into_view_if_needed(timeout=1500)
                            await loc.click(timeout=2000)
                            logger(f"[DEBUG] {group_name}: clicked {selector} (match #{idx})")
                            return selector
                    except Exception as inner_exc:
                        last_error = inner_exc
                        continue
            except Exception as exc:
                last_error = exc
        await page.wait_for_timeout(250)
    raise CheckoutStepError(
        f"Selector group '{group_name}' unresolved. "
        f"TODO validate selectors. Tried={selectors}. LastError={last_error}"
    )


async def _wait_fill(
    page: Page,
    selectors: list[str],
    value: str,
    timeout_ms: int,
    group_name: str,
    logger: Callable[[str], None],
    submit: bool = False,
    post_type_wait_ms: int = 0,
) -> str:
    last_error: Exception | None = None
    for selector in selectors:
        try:
            loc = page.locator(selector).first
            await loc.wait_for(state="visible", timeout=timeout_ms)
            await loc.click(timeout=timeout_ms)
            await loc.press("Control+A")
            await loc.press("Backspace")
            await loc.type(value, delay=40, timeout=timeout_ms)
            if submit:
                await loc.press("Enter")
            if post_type_wait_ms > 0:
                await page.wait_for_timeout(post_type_wait_ms)
            logger(f"[DEBUG] {group_name}: typed into {selector}")
            return selector
        except Exception as exc:
            last_error = exc
    raise CheckoutStepError(
        f"Selector group '{group_name}' unresolved (fill). "
        f"TODO validate selectors. Tried={selectors}. LastError={last_error}"
    )


async def _wait_visible(page: Page, selectors: list[str], timeout_ms: int, group_name: str) -> str:
    last_error: Exception | None = None
    for selector in selectors:
        try:
            loc = page.locator(selector).first
            await loc.wait_for(state="visible", timeout=timeout_ms)
            return selector
        except Exception as exc:
            last_error = exc
    raise CheckoutStepError(
        f"Selector group '{group_name}' unresolved (visible check). "
        f"TODO validate selectors. Tried={selectors}. LastError={last_error}"
    )


async def _try_click(
    page: Page,
    selectors: list[str],
    timeout_ms: int,
    group_name: str,
    logger: Callable[[str], None],
    visible_timeout_ms: int = 1500,
) -> bool:
    for selector in selectors:
        try:
            loc = page.locator(selector).first
            if await loc.is_visible(timeout=visible_timeout_ms):
                await loc.click(timeout=timeout_ms)
                logger(f"[DEBUG] {group_name}: clicked {selector}")
                return True
        except Exception:
            continue
    return False


async def _try_first_text(page: Page, selectors: list[str], timeout_ms: int = 1500) -> str | None:
    for selector in selectors:
        try:
            loc = page.locator(selector).first
            if await loc.is_visible(timeout=timeout_ms):
                text = (await loc.inner_text(timeout=timeout_ms)).strip()
                if text:
                    return text
        except Exception:
            continue
    return None


async def _wait_any_visible(
    page: Page,
    selectors: list[str],
    timeout_ms: int,
    group_name: str,
    logger: Callable[[str], None],
) -> bool:
    deadline = _deadline(timeout_ms)
    while _now_ts() < deadline:
        for selector in selectors:
            try:
                loc = page.locator(selector)
                count = await loc.count()
                for idx in range(count):
                    candidate = loc.nth(idx)
                    if await candidate.is_visible(timeout=700):
                        logger(f"[DEBUG] {group_name}: visible {selector} (match #{idx})")
                        return True
            except Exception:
                continue
        await page.wait_for_timeout(250)
    return False


async def _wait_all_hidden(page: Page, selectors: list[str], timeout_ms: int) -> bool:
    deadline = _deadline(timeout_ms)
    while _now_ts() < deadline:
        any_visible = False
        for selector in selectors:
            try:
                loc = page.locator(selector)
                count = await loc.count()
                for idx in range(count):
                    if await loc.nth(idx).is_visible(timeout=120):
                        any_visible = True
                        break
                if any_visible:
                    break
            except Exception:
                continue
        if not any_visible:
            return True
        await page.wait_for_timeout(120)
    return False


async def _wait_product_modal(page: Page, product_name: str, timeout_ms: int) -> None:
    modal_markers = [
        "div[role='dialog']",
        "div[class*='modal']",
        f"text=/{product_name}/i",
    ]
    if not await _wait_any_visible(
        page, modal_markers, timeout_ms=timeout_ms, group_name="product_modal", logger=lambda _: None
    ):
        raise CheckoutStepError(
            f"Product modal did not become visible for '{product_name}'. "
            "TODO validate product click target and modal selectors."
        )


async def _open_product_modal_exact(
    page: Page,
    product: str,
    timeout_ms: int,
    logger: Callable[[str], None],
) -> None:
    deadline = _deadline(timeout_ms)
    last_error: Exception | None = None
    stagnant_scrolls = 0
    last_scroll_y = -1

    while _now_ts() < deadline:
        try:
            product_norm = _normalize_text(product)
            candidate_groups = [
                ("exact_cs", page.get_by_text(product, exact=True)),
                ("exact_ci_regex", page.get_by_text(rf"/^\s*{_re_escape(product)}\s*$/i")),
                ("loose_text", page.get_by_text(product, exact=False)),
            ]
            for source, loc in candidate_groups:
                count = await loc.count()
                for idx in range(count):
                    candidate = loc.nth(idx)
                    try:
                        if not await candidate.is_visible(timeout=350):
                            continue
                        raw_text = (await candidate.inner_text(timeout=700)).strip()
                        if _normalize_text(raw_text) != product_norm:
                            continue
                        await candidate.scroll_into_view_if_needed(timeout=1500)
                        await candidate.click(timeout=2500)
                        logger(f"[DEBUG] product_card_exact: clicked exact normalized match '{raw_text}' via {source}")
                        await _wait_product_modal(page, product, timeout_ms=min(timeout_ms, 8000))
                        return
                    except Exception as inner_exc:
                        last_error = inner_exc
                        continue
        except Exception as exc:
            last_error = exc

        # Continue scanning catalog by scrolling if exact match is not yet visible.
        try:
            current_scroll_y = await page.evaluate("() => window.scrollY")
            await page.mouse.wheel(0, 900)
            await page.wait_for_timeout(450)
            new_scroll_y = await page.evaluate("() => window.scrollY")
            if new_scroll_y == current_scroll_y:
                stagnant_scrolls += 1
            else:
                stagnant_scrolls = 0
            last_scroll_y = new_scroll_y
            logger(f"[DEBUG] product_card_exact: scanning catalog (scrollY={last_scroll_y})")
            if stagnant_scrolls >= 2:
                break
        except Exception as exc:
            last_error = exc
            break

    raise CheckoutStepError(
        "Exact product match not found/clickable. "
        f"Requested='{product}'. LastError={last_error}"
    )


def _normalize_text(value: str) -> str:
    folded = "".join(
        ch
        for ch in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(ch)
    )
    return " ".join(folded.split()).strip().lower()


def _re_escape(value: str) -> str:
    import re

    return re.escape(value)


async def _click_first_address_suggestion(
    page: Page,
    selectors: list[str],
    timeout_ms: int,
    logger: Callable[[str], None],
) -> None:
    deadline = _deadline(timeout_ms)
    last_error: Exception | None = None
    while _now_ts() < deadline:
        for selector in selectors:
            try:
                loc = page.locator(selector)
                count = await loc.count()
                if count == 0:
                    continue
                first = loc.first
                if await first.is_visible(timeout=600) and await first.is_enabled(timeout=600):
                    await first.scroll_into_view_if_needed(timeout=1500)
                    await first.click(timeout=2000)
                    logger(f"[DEBUG] address_suggestion_button: clicked first match of {selector}")
                    return
            except Exception as exc:
                last_error = exc
                continue
        await page.wait_for_timeout(250)
    raise CheckoutStepError(
        "Selector group 'address_suggestion_button' unresolved. "
        f"Tried={selectors}. LastError={last_error}"
    )


async def _first_visible_dialog(page: Page, timeout_ms: int) -> Any | None:
    deadline = _deadline(timeout_ms)
    selectors = ["section[role='dialog']", "div[role='dialog']"]
    while _now_ts() < deadline:
        for selector in selectors:
            try:
                loc = page.locator(selector)
                count = await loc.count()
                for idx in range(count):
                    dialog = loc.nth(idx)
                    if await dialog.is_visible(timeout=120):
                        return dialog
            except Exception:
                continue
        await page.wait_for_timeout(90)
    return None


def _now_ts() -> float:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).timestamp()


def _deadline(timeout_ms: int) -> float:
    return _now_ts() + (timeout_ms / 1000.0)

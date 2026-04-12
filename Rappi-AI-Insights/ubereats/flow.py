from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from playwright.async_api import Page

from ubereats.cart import clear_cart_if_needed, read_cart_badge_count
from ubereats.errors import CheckoutStepError
from ubereats.location import set_address_if_needed
from ubereats.product_catalog import select_product_card_exact
from ubereats.readiness import (
    wait_for_cart_ready,
    wait_for_cart_state_updated,
    wait_for_checkout_ready,
    wait_for_home_ready,
    wait_for_product_modal_ready,
    wait_for_restaurant_ready,
    wait_for_search_results_ready,
    wait_for_store_search_fully_applied,
)
from ubereats.selectors import build_selectors
from ubereats.ui_actions import safe_click, safe_fill, safe_press_enter, safe_select_first_result


async def login_if_needed(
    page: Page,
    context: Any,
    storage_state_path: Path,
    logger: Callable[[str], None],
) -> None:
    try:
        sign_in = page.get_by_text("Iniciar sesión", exact=False).first
        if await sign_in.is_visible(timeout=1200):
            logger("[STEP 2] Login required, complete login manually in browser.")
            input("When login is completed, press ENTER to continue...")
            await context.storage_state(path=str(storage_state_path))
            logger(f"[INFO] Updated storage state saved: {storage_state_path}")
    except Exception:
        return


async def search_restaurant(
    page: Page,
    restaurant: str,
    selectors: dict[str, list[str]],
    timeout_ms: int,
    logger: Callable[[str], None],
) -> None:
    await wait_for_home_ready(page, timeout_ms=min(timeout_ms, 12000), logger=logger)
    search_sel = selectors["global_search_input"][0]
    await safe_fill(
        page,
        search_sel,
        value=restaurant,
        timeout_ms=min(timeout_ms, 12000),
        logger=logger,
        step_name="global_search_input",
    )
    await safe_press_enter(
        page,
        search_sel,
        timeout_ms=min(timeout_ms, 8000),
        logger=logger,
        step_name="global_search_submit",
    )
    await wait_for_search_results_ready(page, timeout_ms=min(timeout_ms, 12000), logger=logger)


async def open_restaurant_result(
    page: Page,
    restaurant: str,
    selectors: dict[str, list[str]],
    timeout_ms: int,
    logger: Callable[[str], None],
) -> None:
    await safe_select_first_result(
        page,
        selectors["restaurant_fallback"],
        timeout_ms=min(timeout_ms, 9000),
        logger=logger,
        step_name="restaurant_result",
    )
    await wait_for_restaurant_ready(page, timeout_ms=min(timeout_ms, 12000), logger=logger)


async def search_product_in_store(
    page: Page,
    product: str,
    selectors: dict[str, list[str]],
    timeout_ms: int,
    logger: Callable[[str], None],
) -> None:
    await wait_for_restaurant_ready(page, timeout_ms=min(timeout_ms, 12000), logger=logger)
    filled_selector: str | None = None
    last_exc: Exception | None = None
    for selector in selectors["store_search_input"]:
        try:
            await safe_fill(
                page,
                selector,
                value=product,
                timeout_ms=min(timeout_ms, 12000),
                logger=logger,
                step_name="store_search_input",
            )
            filled_selector = selector
            logger(f"[DEBUG] store_search_input_used={selector}")
            break
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            continue
    if not filled_selector:
        raise CheckoutStepError(
            f"Store search input unavailable. Tried={selectors['store_search_input']} LastError={last_exc}"
        )

    await wait_for_store_search_fully_applied(
        page,
        expected_product=product,
        preferred_input_selector=filled_selector,
        timeout_ms=min(timeout_ms, 15000),
        logger=logger,
    )


async def add_product(
    page: Page,
    product: str,
    selectors: dict[str, list[str]],
    timeout_ms: int,
    logger: Callable[[str], None],
) -> None:
    badge_before = await read_cart_badge_count(page, selectors["cart_badge"][0])
    await select_product_card_exact(page, product=product, timeout_ms=timeout_ms, logger=logger)

    modal_ready = await wait_for_product_modal_ready(
        page,
        timeout_ms=min(timeout_ms, 12000),
        logger=logger,
    )
    if modal_ready:
        await safe_click(
            page,
            selectors["confirm_add"][0],
            timeout_ms=min(timeout_ms, 9000),
            logger=logger,
            step_name="confirm_add_button",
        )

    transition_updated = await wait_for_cart_state_updated(
        page,
        previous_badge_count=badge_before,
        selectors=selectors,
        timeout_ms=min(timeout_ms, 12000),
        logger=logger,
    )
    if not transition_updated:
        raise CheckoutStepError("Cart state did not update after add-product action (post-click transition not confirmed).")

    if "/checkout" in page.url.lower():
        logger("[DEBUG] post_add_transition: already on checkout URL")
        return

    continue_ready = False
    try:
        await page.wait_for_selector(selectors["go_checkout"][0], state="visible", timeout=min(8000, timeout_ms))
        continue_ready = True
    except Exception:
        continue_ready = False

    if continue_ready:
        await safe_click(
            page,
            selectors["go_checkout"][0],
            timeout_ms=min(8000, timeout_ms),
            logger=logger,
            step_name="post_add_continue",
        )
        return

    cart_opened = False
    if await page.locator(selectors["cart_open"][0]).first.is_visible(timeout=3000):
        await safe_click(
            page,
            selectors["cart_open"][0],
            timeout_ms=min(10000, timeout_ms),
            logger=logger,
            step_name="post_add_open_cart",
            allow_force=True,
        )
        await wait_for_cart_ready(
            page,
            timeout_ms=min(timeout_ms, 12000),
            logger=logger,
            selectors=selectors,
            previous_badge_count=badge_before,
        )
        cart_opened = True

    if await page.locator(selectors["go_checkout"][0]).first.is_visible(timeout=4000):
        await safe_click(
            page,
            selectors["go_checkout"][0],
            timeout_ms=min(8000, timeout_ms),
            logger=logger,
            step_name="post_add_continue_from_cart",
        )
    else:
        if cart_opened:
            logger("[DEBUG] post_add_continue: cart opened but 'Continuar' not visible yet; Step 9 will handle checkout transition.")
        else:
            logger("[DEBUG] post_add_continue: cart not opened and 'Continuar' not visible; Step 9 will handle checkout transition.")


async def go_to_checkout(
    page: Page,
    selectors: dict[str, list[str]],
    timeout_ms: int,
    logger: Callable[[str], None],
) -> None:
    if "/checkout" in page.url.lower():
        logger("[DEBUG] go_checkout: already on checkout URL")
        return
    if not await page.locator(selectors["go_checkout"][0]).first.is_visible(timeout=3000):
        await safe_click(
            page,
            selectors["cart_open"][0],
            timeout_ms=min(timeout_ms, 12000),
            logger=logger,
            step_name="cart_open_checkout",
            ready_selector=selectors["global_search_input"][0],
            allow_force=True,
        )
        await wait_for_cart_ready(
            page,
            timeout_ms=min(timeout_ms, 12000),
            logger=logger,
            selectors=selectors,
        )

    await safe_click(
        page,
        selectors["go_checkout"][0],
        timeout_ms=min(timeout_ms, 10000),
        logger=logger,
        step_name="go_checkout",
    )
    if await page.locator(selectors["skip_upsell"][0]).first.is_visible(timeout=3000):
        await safe_click(
            page,
            selectors["skip_upsell"][0],
            timeout_ms=min(timeout_ms, 6000),
            logger=logger,
            step_name="skip_upsell",
        )


async def confirm_checkout_visible(
    page: Page,
    selectors: dict[str, list[str]],
    timeout_ms: int,
    logger: Callable[[str], None],
) -> None:
    await wait_for_checkout_ready(
        page,
        selectors=selectors,
        collector=None,
        timeout_ms=min(timeout_ms, 15000),
        logger=logger,
    )

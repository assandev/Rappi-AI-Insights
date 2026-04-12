from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from typing import Callable

from playwright.async_api import Page

from ubereats.errors import CheckoutStepError
from ubereats.ui_actions import safe_click


def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


def _deadline(timeout_ms: int) -> float:
    return _now_ts() + (timeout_ms / 1000.0)


def normalize_product_text(value: str) -> str:
    folded = "".join(
        ch
        for ch in unicodedata.normalize("NFKD", str(value))
        if not unicodedata.combining(ch)
    )
    cleaned = re.sub(r"[^a-z0-9\s]", " ", folded.lower())
    return " ".join(cleaned.split())


async def select_product_card_exact(
    page: Page,
    product: str,
    timeout_ms: int,
    logger: Callable[[str], None],
) -> None:
    target = normalize_product_text(product)
    stagnant = 0
    deadline = _deadline(timeout_ms)
    logger(f"[DEBUG] quick_add_exact: target_raw='{product}' target_norm='{target}'")

    while _now_ts() < deadline:
        # Rollback behavior: search exact visible product labels and click quick-add in same card.
        exact = page.get_by_text(product, exact=True)
        if await exact.count() == 0:
            exact = page.get_by_text(re.compile(rf"^\s*{re.escape(product)}\s*$", re.IGNORECASE))

        count = await exact.count()
        for idx in range(count):
            label = exact.nth(idx)
            try:
                if not await label.is_visible(timeout=180):
                    continue
                raw = (await label.inner_text(timeout=300) or "").strip()
                if normalize_product_text(raw) != target:
                    continue

                card = label.locator(
                    "xpath=ancestor::*[self::li or self::article or self::section or self::div][.//button[@data-testid='quick-add-button']][1]"
                ).first
                quick_btn = card.locator("button[data-testid='quick-add-button']").first
                if await quick_btn.is_visible(timeout=250):
                    await safe_click(
                        page,
                        quick_btn,
                        timeout_ms=2500,
                        logger=logger,
                        step_name="quick_add_exact",
                        max_attempts=4,
                    )
                    logger(f"[DEBUG] quick_add_exact: clicked exact product '{raw}' (match #{idx})")
                    return

                # Fallback for variants where + button has no testid.
                card_fallback = label.locator(
                    "xpath=ancestor::*[self::li or self::article or self::section or self::div][.//button][1]"
                ).first
                plus_btn = card_fallback.locator(
                    "button[aria-label*='Agregar' i], button[aria-label*='Add' i], button:has-text('+')"
                ).first
                if not await plus_btn.is_visible(timeout=250):
                    continue
                await safe_click(
                    page,
                    plus_btn,
                    timeout_ms=2500,
                    logger=logger,
                    step_name="quick_add_exact",
                    max_attempts=4,
                )
                logger(f"[DEBUG] quick_add_exact: clicked fallback plus for exact product '{raw}' (match #{idx})")
                return
            except Exception:
                continue

        before = await page.evaluate("() => window.scrollY")
        await page.mouse.wheel(0, 900)
        try:
            await page.wait_for_function("(beforeY) => window.scrollY !== beforeY", before, timeout=min(timeout_ms, 1500))
        except Exception:
            pass
        after = await page.evaluate("() => window.scrollY")
        logger(f"[DEBUG] quick_add_exact: scanning catalog (scrollY={int(after)})")
        stagnant = stagnant + 1 if after == before else 0
        if stagnant >= 2:
            break

    raise CheckoutStepError(f"Exact product not found for quick-add: '{product}'")

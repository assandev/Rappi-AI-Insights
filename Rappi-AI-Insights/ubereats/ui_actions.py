from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Union

from playwright.async_api import Locator, Page

from ubereats.errors import CheckoutStepError


def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


def _is_pointer_intercept_error(exc: Exception) -> bool:
    low = str(exc).lower()
    return "intercepts pointer events" in low or "subtree intercepts pointer events" in low


async def wait_ui_not_busy(page: Page, timeout_ms: int) -> None:
    try:
        await page.wait_for_function(
            """
            () => {
                const hasBusy = !!document.querySelector('[aria-busy="true"]');
                const hasProgress = !!document.querySelector('[role="progressbar"]');
                const hasSkeleton = !!document.querySelector('[data-testid*="skeleton" i], [class*="skeleton"]');
                return !hasBusy && !hasProgress && !hasSkeleton;
            }
            """,
            timeout=timeout_ms,
        )
    except Exception:
        pass


async def trial_clickable(page: Page, selector: str, timeout_ms: int = 400) -> bool:
    try:
        await page.locator(selector).first.click(timeout=timeout_ms, trial=True)
        return True
    except Exception:
        return False


async def safe_click(
    page: Page,
    selector_or_locator: Union[str, Locator],
    *,
    timeout_ms: int,
    logger: Callable[[str], None],
    step_name: str,
    ready_selector: str | None = None,
    post_click_selector: str | None = None,
    max_attempts: int = 5,
    allow_force: bool = False,
) -> None:
    if ready_selector:
        await page.wait_for_selector(ready_selector, state="visible", timeout=min(timeout_ms, 8000))
        await wait_ui_not_busy(page, timeout_ms=min(timeout_ms, 1200))

    loc = page.locator(selector_or_locator).first if isinstance(selector_or_locator, str) else selector_or_locator.first
    await loc.wait_for(state="visible", timeout=timeout_ms)

    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            await wait_ui_not_busy(page, timeout_ms=min(timeout_ms, 1300))
            await loc.scroll_into_view_if_needed(timeout=min(timeout_ms, 1200))
            await loc.click(timeout=min(timeout_ms, 1800))
            if post_click_selector:
                await page.wait_for_selector(post_click_selector, state="visible", timeout=min(timeout_ms, 2500))
            logger(f"[DEBUG] {step_name}: click succeeded on attempt {attempt}")
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if allow_force and attempt == max_attempts:
                try:
                    await loc.click(timeout=min(timeout_ms, 1500), force=True)
                    logger(f"[DEBUG] {step_name}: force-click succeeded on attempt {attempt}")
                    return
                except Exception as force_exc:  # noqa: BLE001
                    last_error = force_exc
            if attempt < max_attempts:
                if _is_pointer_intercept_error(exc):
                    logger(f"[DEBUG] {step_name}: click attempt {attempt} intercepted, retrying")
                else:
                    logger(f"[DEBUG] {step_name}: click attempt {attempt} failed, retrying")
                await wait_ui_not_busy(page, timeout_ms=min(450, 150 + attempt * 60))
                continue
    raise CheckoutStepError(f"{step_name} click failed after {max_attempts} attempts. LastError={last_error}")


async def safe_fill(
    page: Page,
    selector_or_locator: Union[str, Locator],
    *,
    value: str,
    timeout_ms: int,
    logger: Callable[[str], None],
    step_name: str,
) -> None:
    loc = page.locator(selector_or_locator).first if isinstance(selector_or_locator, str) else selector_or_locator.first
    await loc.wait_for(state="visible", timeout=timeout_ms)
    await loc.click(timeout=min(timeout_ms, 1400))
    await loc.press("Control+A")
    await loc.press("Backspace")
    await loc.type(value, delay=25, timeout=timeout_ms)
    logger(f"[DEBUG] {step_name}: typed value")


async def safe_press_enter(
    page: Page,
    selector_or_locator: Union[str, Locator],
    *,
    timeout_ms: int,
    logger: Callable[[str], None],
    step_name: str,
) -> None:
    loc = page.locator(selector_or_locator).first if isinstance(selector_or_locator, str) else selector_or_locator.first
    await loc.wait_for(state="visible", timeout=timeout_ms)
    await loc.press("Enter", timeout=min(timeout_ms, 1100))
    logger(f"[DEBUG] {step_name}: pressed Enter")


async def safe_select_first_result(
    page: Page,
    selectors: list[str],
    *,
    timeout_ms: int,
    logger: Callable[[str], None],
    step_name: str,
) -> str:
    deadline = _now_ts() + (timeout_ms / 1000.0)
    last_error: Exception | None = None
    while _now_ts() < deadline:
        for selector in selectors:
            try:
                loc = page.locator(selector)
                count = await loc.count()
                if count == 0:
                    continue
                for idx in range(count):
                    candidate = loc.nth(idx)
                    if not await candidate.is_visible(timeout=180):
                        continue
                    await safe_click(
                        page,
                        candidate,
                        timeout_ms=min(timeout_ms, 3000),
                        logger=logger,
                        step_name=step_name,
                        max_attempts=4,
                    )
                    return selector
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                continue
        await wait_ui_not_busy(page, timeout_ms=300)
    raise CheckoutStepError(f"{step_name} unresolved. Tried={selectors}. LastError={last_error}")


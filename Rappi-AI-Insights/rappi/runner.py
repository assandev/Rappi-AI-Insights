from __future__ import annotations
import asyncio
import re
from datetime import datetime, timezone
from typing import Callable

from playwright.async_api import Browser, Page

from rappi import config
from rappi.extract import extract_eta_context, extract_restaurant_context, extract_totals_from_payloads
from rappi.flow import (
    add_product,
    build_selectors,
    clear_cart_if_needed,
    close_last_craving_modal_if_present,
    go_to_checkout,
    login_if_needed,
    open_restaurant,
    set_address_if_needed,
)
from rappi.io_utils import iso_now, save_screenshot, step_log, write_jsonl
from rappi.models import CheckoutJob, CheckoutResult, slugify_product
from rappi.network import NetworkCollector


def build_jobs() -> list[CheckoutJob]:
    jobs: list[CheckoutJob] = []
    for address in config.ADDRESSES:
        if not isinstance(address, dict):
            # Guard against accidental string/comment entries inside ADDRESSES.
            continue
        required_keys = {"address_id", "address_text", "zone_type"}
        if not required_keys.issubset(address.keys()):
            continue
        for product in config.PRODUCTS:
            jobs.append(
                CheckoutJob(
                    platform=config.PLATFORM,
                    address_id=address["address_id"],
                    address_text=address["address_text"],
                    zone_type=address["zone_type"],
                    restaurant=config.RESTAURANT,
                    product=product,
                    storage_state=config.STORAGE_STATE,
                    screenshot_dir=config.SCREENSHOT_DIR,
                    network_log_file=config.NETWORK_LOG_FILE,
                    result_file=config.RESULT_FILE,
                    timeout_ms=config.TIMEOUT_MS,
                    include_body=config.INCLUDE_BODY,
                    body_max_chars=config.BODY_MAX_CHARS,
                )
            )
    return jobs


async def run_single_checkout(
    job: CheckoutJob,
    browser: Browser,
    logger: Callable[[str], None] | None = None,
) -> CheckoutResult:
    log = logger or (lambda _: None)
    current_step = 0

    if not job.storage_state.exists():
        return CheckoutResult(
            ts=iso_now(),
            status="error",
            failed_step=0,
            error_type="MissingStorageState",
            error_message=f"Missing storage state. Expected file: {job.storage_state}",
            platform=job.platform,
            address=job.address_text,
            restaurant=job.restaurant,
            product=job.product,
            network_log_file="",
            matched_requests=0,
        )

    run_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_day = run_ts[:8]
    product_slug = slugify_product(job.product)
    zone_slug = _slug_segment(job.zone_type)
    job_bucket = f"a{job.address_id}_{zone_slug}_{product_slug}"

    screenshot_dir = job.screenshot_dir / "runs" / run_day / job_bucket
    network_dir = job.network_log_file.parent / "runs" / run_day / job_bucket

    network_log_path = network_dir / f"checkout_network_{job.platform}_{run_ts}.jsonl"
    screenshot_success = screenshot_dir / f"checkout_{job.platform}_{run_ts}_success.png"
    screenshot_error = screenshot_dir / f"checkout_{job.platform}_{run_ts}_error.png"

    selectors = build_selectors(job.restaurant, job.product)
    context = await browser.new_context(storage_state=str(job.storage_state), locale="es-MX")
    context.set_default_timeout(job.timeout_ms)
    context.set_default_navigation_timeout(job.timeout_ms)
    page = await context.new_page()

    collector = NetworkCollector(
        include_body=job.include_body,
        body_max_chars=job.body_max_chars,
        log_writer=lambda payload: write_jsonl(network_log_path, payload),
        logger=None,
    )

    async def _on_response(resp):
        await collector.on_response(resp)

    page.on("response", lambda resp: asyncio.create_task(_on_response(resp)))
    log(f"[JOB START] a{job.address_id} {job.zone_type} | {job.product}")

    try:
        current_step = 1
        collector.set_step(current_step)
        step_log(log, current_step, f"Open app a{job.address_id}")
        await page.goto(config.START_URL, wait_until="domcontentloaded")
        await page.wait_for_load_state("networkidle")

        current_step = 2
        collector.set_step(current_step)
        step_log(log, current_step, "Login check")
        await login_if_needed(page, context, job.storage_state, logger=log)
        await page.wait_for_load_state("networkidle")

        current_step = 3
        collector.set_step(current_step)
        step_log(log, current_step, "Clear cart if needed")
        await clear_cart_if_needed(page, collector.payloads, timeout_ms=job.timeout_ms, logger=log)
        await page.wait_for_load_state("networkidle")

        current_step = 4
        collector.set_step(current_step)
        step_log(log, current_step, "Set address")
        await set_address_if_needed(
            page,
            address=job.address_text,
            selectors=selectors,
            timeout_ms=job.timeout_ms,
            logger=log,
        )

        current_step = 5
        collector.set_step(current_step)
        step_log(log, current_step, "Open restaurant")
        await open_restaurant(
            page,
            restaurant=job.restaurant,
            selectors=selectors,
            timeout_ms=job.timeout_ms,
            logger=log,
        )

        current_step = 6
        collector.set_step(current_step)
        step_log(log, current_step, "Add product")
        await add_product(
            page,
            product=job.product,
            selectors=selectors,
            timeout_ms=job.timeout_ms,
            logger=log,
        )

        current_step = 7
        collector.set_step(current_step)
        collector.set_priority_window(True)
        step_log(log, current_step, "Go checkout")
        await go_to_checkout(page, selectors=selectors, timeout_ms=job.timeout_ms, logger=log)

        current_step = 8
        collector.set_step(current_step)
        step_log(log, current_step, "Wait checkout payloads")
        await close_last_craving_modal_if_present(page, selectors=selectors, logger=log)
        await _wait_required_payloads_async(page, collector, timeout_ms=job.timeout_ms, logger=log)
        collector.set_priority_window(False)

        current_step = 9
        collector.set_step(current_step)
        totals = extract_totals_from_payloads(collector.payloads)
        eta_ctx = await extract_eta_context(page, collector.payloads)

        current_step = 10
        collector.set_step(current_step)
        screenshot_path = await save_screenshot(page, screenshot_success, logger=log)

        current_step = 11
        collector.set_step(current_step)
        restaurant_ctx = await extract_restaurant_context(page, collector.payloads, fallback_name=job.restaurant)
        top_endpoints = [e.to_dict() for e in collector.top_endpoints(limit=12)]
        if collector.matched_requests == 0:
            raise RuntimeError("Checkout visible but no checkout/cart endpoint captured.")

        result = CheckoutResult(
            ts=iso_now(),
            status="success",
            platform=job.platform,
            address=job.address_text,
            restaurant=restaurant_ctx.restaurant_name,
            restaurant_address=restaurant_ctx.restaurant_address,
            restaurant_source=restaurant_ctx.source,
            product=job.product,
            checkout_url=page.url,
            subtotal=totals.subtotal,
            delivery_fee=totals.delivery_fee,
            service_fee=totals.service_fee,
            total=totals.total,
            eta_min_minutes=eta_ctx.eta_min_minutes,
            eta_max_minutes=eta_ctx.eta_max_minutes,
            eta_avg_minutes=eta_ctx.eta_avg_minutes,
            eta_source=eta_ctx.eta_source,
            currency="MXN",
            screenshot_path=str(screenshot_path) if screenshot_path else None,
            network_log_file=str(network_log_path),
            matched_requests=collector.matched_requests,
            top_endpoints=top_endpoints,
        )
        log(f"[JOB OK] a{job.address_id} {job.product} total={result.total}")
        return result
    except Exception as exc:  # noqa: BLE001
        log(f"[JOB ERR] a{job.address_id} {job.product} step={current_step} {type(exc).__name__}: {exc}")
        screenshot_path = await save_screenshot(page, screenshot_error, logger=log)
        return CheckoutResult(
            ts=iso_now(),
            status="error",
            failed_step=current_step,
            error_type=type(exc).__name__,
            error_message=str(exc),
            platform=job.platform,
            address=job.address_text,
            restaurant=job.restaurant,
            restaurant_address=None,
            restaurant_source="fallback",
            product=job.product,
            checkout_url=page.url,
            eta_min_minutes=None,
            eta_max_minutes=None,
            eta_avg_minutes=None,
            eta_source="none",
            screenshot_path=str(screenshot_path) if screenshot_path else None,
            network_log_file=str(network_log_path),
            matched_requests=collector.matched_requests,
        )
    finally:
        await context.close()


def _concurrency_key_for_job(job: CheckoutJob) -> tuple[str, str]:
    # Future hook only:
    # later we can schedule by (platform, account_id) to allow safe parallelism
    # across different authenticated accounts.
    account_key = str(job.storage_state)
    return (job.platform, account_key)


def _slug_segment(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "segment"


async def run_jobs_serially(
    jobs: list[CheckoutJob],
    browser: Browser,
    logger: Callable[[str], None] | None = None,
) -> list[CheckoutResult]:
    log = logger or (lambda _: None)
    normalized: list[CheckoutResult] = []
    for job in jobs:
        # Execution policy:
        # Browser contexts/pages are NOT enough isolation for Rappi cart state.
        # Cart is shared server-side per account/session, so jobs with the same
        # (platform, account) must run serially.
        _ = _concurrency_key_for_job(job)
        try:
            result = await run_single_checkout(job, browser, logger=log)
        except Exception as exc:  # noqa: BLE001
            result = CheckoutResult(
                ts=iso_now(),
                status="error",
                failed_step=0,
                error_type=type(exc).__name__,
                error_message=str(exc),
                platform=job.platform,
                address=job.address_text,
                restaurant=job.restaurant,
                product=job.product,
                network_log_file="",
                matched_requests=0,
            )
        normalized.append(result)
    return normalized


async def _wait_required_payloads_async(
    page: Page,
    collector: NetworkCollector,
    timeout_ms: int,
    logger: Callable[[str], None],
) -> None:
    required = {"all_get", "summary_v2", "checkout_detail"}
    start_ms = await page.evaluate("Date.now()")
    deadline_ms = start_ms + timeout_ms
    while True:
        now_ms = await page.evaluate("Date.now()")
        if now_ms >= deadline_ms:
            break
        present = set(collector.payloads.keys())
        if required.issubset(present):
            logger(f"[DEBUG] Required payloads captured: {', '.join(sorted(required))}")
            return
        await page.wait_for_timeout(200)
    present = set(collector.payloads.keys())
    missing = sorted(required - present)
    raise RuntimeError(
        "Required checkout payloads not captured in time. "
        f"Missing={missing}; Present={sorted(present)}"
    )

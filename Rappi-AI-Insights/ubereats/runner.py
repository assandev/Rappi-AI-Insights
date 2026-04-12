from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import Awaitable, Callable

from playwright.async_api import Browser

from rappi.io_utils import iso_now, save_screenshot, step_log, write_json, write_jsonl
from rappi.models import CheckoutJob, CheckoutResult, slugify_product
from ubereats import config
from ubereats.debug_dump import dump_checkout_debug_artifacts
from ubereats.extract import (
    extract_cart_item_title,
    extract_ubereats_checkout_payload,
    extract_ubereats_totals_from_dom,
    extract_ubereats_totals_from_network,
    extract_eta_context,
    extract_restaurant_context,
    save_checkout_dom_snapshot,
    validate_ubereats_result,
)
from ubereats.flow import (
    add_product,
    build_selectors,
    clear_cart_if_needed,
    confirm_checkout_visible,
    go_to_checkout,
    login_if_needed,
    open_restaurant_result,
    search_product_in_store,
    search_restaurant,
    set_address_if_needed,
    wait_for_checkout_ready,
    wait_for_home_ready,
)
from ubereats.network import NetworkCollector


def build_jobs() -> list[CheckoutJob]:
    jobs: list[CheckoutJob] = []
    for address in config.ADDRESSES:
        if not isinstance(address, dict):
            continue
        required = {"address_id", "address_text", "zone_type"}
        if not required.issubset(address.keys()):
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


async def retry_once_after_settle(
    action_coro: Callable[[], Awaitable[None]],
    *,
    page,
    logger: Callable[[str], None],
    step_name: str,
    retry_wait_ms: int = 2000,
) -> None:
    try:
        await action_coro()
    except Exception:
        logger(f"[DEBUG] {step_name}: first attempt failed, retrying after {retry_wait_ms}ms")
        await page.wait_for_timeout(retry_wait_ms)
        await action_coro()


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
    zone_slug = _slug_segment(job.zone_type)
    product_slug = slugify_product(job.product)
    job_bucket = f"a{job.address_id}_{zone_slug}_{product_slug}"

    screenshot_dir = job.screenshot_dir / "runs" / run_day / job_bucket
    network_dir = job.network_log_file.parent / "runs" / run_day / job_bucket

    network_log_path = network_dir / f"checkout_network_{job.platform}_{run_ts}.jsonl"
    checkout_candidates_path = network_dir / f"checkout_candidates_{job.platform}_{run_ts}.jsonl"
    checkout_payload_path = network_dir / f"checkout_payloads_{job.platform}_{run_ts}.json"
    checkout_dom_snapshot_path = network_dir / f"checkout_dom_snapshot_{job.platform}_{run_ts}.json"
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
        debug_capture_bodies=config.DEBUG_CHECKOUT_MODE,
        debug_max_bodies=config.DEBUG_MAX_NETWORK_BODIES,
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
        step_log(log, current_step, "Open app")
        await page.goto(config.START_URL, wait_until="domcontentloaded")
        await wait_for_home_ready(page, timeout_ms=min(job.timeout_ms, 14000), logger=log)
        log("[DEBUG] home_ready_settle: waiting 3000ms")
        await page.wait_for_timeout(3000)

        current_step = 2
        collector.set_step(current_step)
        step_log(log, current_step, "Login check")
        await login_if_needed(page, context, job.storage_state, logger=log)

        current_step = 3
        collector.set_step(current_step)
        step_log(log, current_step, "Clear cart")
        await retry_once_after_settle(
            lambda: clear_cart_if_needed(page, selectors=selectors, timeout_ms=job.timeout_ms, logger=log),
            page=page,
            logger=log,
            step_name="clear_cart_after_home_ready",
            retry_wait_ms=2000,
        )

        current_step = 4
        collector.set_step(current_step)
        step_log(log, current_step, "Change location")
        await set_address_if_needed(page, address=job.address_text, selectors=selectors, timeout_ms=job.timeout_ms, logger=log)

        current_step = 5
        collector.set_step(current_step)
        step_log(log, current_step, "Search restaurant")
        await search_restaurant(page, restaurant=job.restaurant, selectors=selectors, timeout_ms=job.timeout_ms, logger=log)

        current_step = 6
        collector.set_step(current_step)
        step_log(log, current_step, "Open restaurant")
        await open_restaurant_result(page, restaurant=job.restaurant, selectors=selectors, timeout_ms=job.timeout_ms, logger=log)

        current_step = 7
        collector.set_step(current_step)
        step_log(log, current_step, "Search product")
        await search_product_in_store(page, product=job.product, selectors=selectors, timeout_ms=job.timeout_ms, logger=log)

        current_step = 8
        collector.set_step(current_step)
        step_log(log, current_step, "Add product")
        await add_product(page, product=job.product, selectors=selectors, timeout_ms=job.timeout_ms, logger=log)

        current_step = 9
        collector.set_step(current_step)
        collector.set_priority_window(True)
        collector.reset_payloads()
        step_log(log, current_step, "Go checkout")
        await go_to_checkout(page, selectors=selectors, timeout_ms=job.timeout_ms, logger=log)

        current_step = 10
        collector.set_step(current_step)
        step_log(log, current_step, "Wait checkout payloads")
        await confirm_checkout_visible(page, selectors=selectors, timeout_ms=job.timeout_ms, logger=log)
        await wait_for_checkout_ready(
            page,
            selectors=selectors,
            collector=collector,
            timeout_ms=job.timeout_ms,
            logger=log,
        )
        collector.set_priority_window(False)

        if config.DEBUG_CHECKOUT_MODE:
            debug_dir = config.DEBUG_CHECKOUT_DIR / f"{run_ts}_{job_bucket}"
            debug_artifacts = await dump_checkout_debug_artifacts(
                page=page,
                collector=collector,
                output_dir=debug_dir,
                logger=log,
            )
            return CheckoutResult(
                ts=iso_now(),
                status="partial",
                platform=job.platform,
                address=job.address_text,
                restaurant=job.restaurant,
                restaurant_address=None,
                restaurant_source="fallback",
                product=job.product,
                checkout_url=page.url,
                screenshot_path=debug_artifacts.get("screenshot_path"),
                network_log_file=str(network_log_path),
                matched_requests=collector.matched_requests,
                extraction_warning="Debug checkout mode enabled; artifacts dumped, extraction skipped.",
                extraction_source="debug",
                checkout_payload_file=debug_artifacts.get("checkout_payload_file"),
                checkout_dom_snapshot_file=debug_artifacts.get("selector_index_file"),
                checkout_candidates_file=debug_artifacts.get("network_index_file"),
            )

        write_json(
            checkout_payload_path,
            {
                "ts": iso_now(),
                "step": current_step,
                "url": page.url,
                "payload_steps": collector.payload_steps,
                "payloads": collector.payloads,
            },
        )
        candidate_rows = collector.checkout_candidate_summary(limit=250)
        for row in candidate_rows:
            write_jsonl(checkout_candidates_path, row)
        dom_snapshot_file = await save_checkout_dom_snapshot(page, checkout_dom_snapshot_path)
        if candidate_rows:
            top_candidate_urls = []
            seen = set()
            for row in candidate_rows:
                url = row.get("url", "")
                if url and url not in seen:
                    top_candidate_urls.append(url)
                    seen.add(url)
                if len(top_candidate_urls) >= 5:
                    break
            log(f"[DEBUG] checkout_candidates_top={len(candidate_rows)} urls={top_candidate_urls}")

        checkout_payloads = extract_ubereats_checkout_payload(collector.payloads)
        network_totals = extract_ubereats_totals_from_network(collector.payloads, product=job.product)
        if checkout_payloads is None:
            dom_totals = await extract_ubereats_totals_from_dom(page)
            totals = dom_totals
            extraction_source = "dom_fallback"
        else:
            totals = network_totals
            extraction_source = "payload:checkout_presentation"

        extraction_warning, validation_error_type, validation_error_message = validate_ubereats_result(
            pricing=totals,
            expected_product=job.product,
            checkout_payloads=checkout_payloads,
        )
        final_status = "success" if extraction_warning is None else "partial"
        if extraction_warning:
            log(f"[WARN] extraction_validation: {extraction_warning}")
        extracted_cart_title = extract_cart_item_title(checkout_payloads)

        eta_ctx = await extract_eta_context(page, collector.payloads, run_ts_utc=iso_now())
        restaurant_ctx = await extract_restaurant_context(page, collector.payloads, fallback_name=job.restaurant)
        screenshot_path = await save_screenshot(page, screenshot_success, logger=log)

        if collector.matched_requests == 0:
            raise RuntimeError("Checkout reached but no checkout/cart endpoint captured.")

        result = CheckoutResult(
            ts=iso_now(),
            status=final_status,
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
            eta_range_text_raw=eta_ctx.eta_range_text_raw,
            currency="MXN",
            screenshot_path=str(screenshot_path) if screenshot_path else None,
            network_log_file=str(network_log_path),
            matched_requests=collector.matched_requests,
            top_endpoints=[e.to_dict() for e in collector.top_endpoints(limit=12)],
            extraction_warning=extraction_warning,
            extraction_source=extraction_source,
            checkout_payload_file=str(checkout_payload_path),
            checkout_dom_snapshot_file=dom_snapshot_file,
            checkout_candidates_file=str(checkout_candidates_path),
            extracted_cart_item_title=extracted_cart_title,
            error_type=validation_error_type,
            error_message=validation_error_message,
        )
        log(f"[JOB {result.status.upper()}] a{job.address_id} {job.product} total={result.total}")
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
            screenshot_path=str(screenshot_path) if screenshot_path else None,
            network_log_file=str(network_log_path),
            matched_requests=collector.matched_requests,
        )
    finally:
        await context.close()


async def run_jobs_serially(
    jobs: list[CheckoutJob],
    browser: Browser,
    logger: Callable[[str], None] | None = None,
) -> list[CheckoutResult]:
    log = logger or (lambda _: None)
    results: list[CheckoutResult] = []
    for job in jobs:
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
        results.append(result)
    return results


def _slug_segment(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "segment"

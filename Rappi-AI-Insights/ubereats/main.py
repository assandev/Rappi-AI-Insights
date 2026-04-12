from __future__ import annotations

import asyncio

from playwright.async_api import async_playwright

from rappi.io_utils import write_json_array
from ubereats import config
from ubereats.runner import build_jobs, run_jobs_serially


async def main_async() -> None:
    jobs = build_jobs()
    if config.DEBUG_CHECKOUT_MODE and jobs:
        print("[DEBUG MODE] checkout forensic dump enabled; forcing single-run job.")
        jobs = jobs[:1]
    effective_concurrency = config.MAX_CONCURRENCY
    if effective_concurrency > 1:
        print(
            "[WARN] MAX_CONCURRENCY > 1 requested, but same-account Uber Eats jobs are serialized. "
            "Clamping to 1."
        )
        effective_concurrency = 1

    print("====================================")
    print(f"platform={config.PLATFORM}")
    print(f"jobs={len(jobs)}")
    print(f"max_concurrency={effective_concurrency}")
    print(f"result_file={config.RESULT_FILE}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=120)
        try:
            results = await run_jobs_serially(jobs=jobs, browser=browser, logger=print)
        finally:
            try:
                await browser.close()
            except Exception as exc:  # noqa: BLE001
                print(f"[WARN] Browser close issue ignored: {type(exc).__name__}: {exc}")

    payload = [result.to_dict() for result in results]
    write_json_array(config.RESULT_FILE, payload)

    success_count = sum(1 for r in results if r.status == "success")
    partial_count = sum(1 for r in results if r.status == "partial")
    error_count = len(results) - success_count - partial_count
    success_rate = (success_count / len(results) * 100.0) if results else 0.0
    print("====================================")
    print(f"completed={len(results)} success={success_count} partial={partial_count} error={error_count}")
    print(f"scraper_success_rate={success_rate:.2f}%")
    print(f"aggregate_result_file={config.RESULT_FILE}")


def main() -> int:
    asyncio.run(main_async())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import asyncio

from playwright.async_api import async_playwright

from rappi import config
from rappi.io_utils import write_json_array
from rappi.runner import build_jobs, run_jobs_serially


async def main_async() -> None:
    jobs = build_jobs()
    effective_concurrency = config.MAX_CONCURRENCY
    if effective_concurrency > 1:
        print(
            "[WARN] MAX_CONCURRENCY > 1 requested, but same-account Rappi jobs are serialized "
            "to avoid backend cart collisions. Clamping to 1."
        )
        effective_concurrency = 1

    print("====================================")
    print(f"jobs={len(jobs)}")
    print(f"max_concurrency={effective_concurrency}")
    print(f"result_file={config.RESULT_FILE}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=120)
        try:
            results = await run_jobs_serially(
                jobs=jobs,
                browser=browser,
                logger=print,
            )
        finally:
            await browser.close()

    payload = [result.to_dict() for result in results]
    write_json_array(config.RESULT_FILE, payload)

    success_count = sum(1 for r in results if r.status == "success")
    error_count = len(results) - success_count
    success_rate = (success_count / len(results) * 100.0) if results else 0.0
    print("====================================")
    print(f"completed={len(results)} success={success_count} error={error_count}")
    print(f"scraper_success_rate={success_rate:.2f}%")
    print(f"aggregate_result_file={config.RESULT_FILE}")


def main() -> int:
    asyncio.run(main_async())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

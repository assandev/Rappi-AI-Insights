from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> int:
    state_path = Path("auth/state_ubereats.json")
    screenshot_path = Path("data/ubereats/screenshots/ubereats_login_state.png")

    state_path.parent.mkdir(parents=True, exist_ok=True)
    screenshot_path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=120)
        context = browser.new_context(locale="es-MX")
        page = context.new_page()
        page.goto("https://www.ubereats.com/mx", wait_until="domcontentloaded")

        print("Complete Uber Eats login manually in the opened browser.")
        input("When fully logged in, press ENTER here to save session state... ")

        context.storage_state(path=str(state_path))
        page.screenshot(path=str(screenshot_path), full_page=True)
        browser.close()

    print(f"[OK] Saved storage state: {state_path}")
    print(f"[OK] Saved verification screenshot: {screenshot_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

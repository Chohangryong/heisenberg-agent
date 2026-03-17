"""Capture latest page after login — uses .env for credentials, auth_state for reuse."""

import os
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

load_dotenv()

AUTH_STATE = Path("data/runtime/auth_state.json")
OUT_DIR = Path("/tmp")


def main():
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)

    # Reuse auth_state if available
    if AUTH_STATE.exists():
        context = browser.new_context(storage_state=str(AUTH_STATE))
        print(f"Loaded auth_state from {AUTH_STATE}")
    else:
        context = browser.new_context()
        print("No auth_state found, logging in fresh")

    page = context.new_page()

    # Always login to ensure auth_state is saved
    if not AUTH_STATE.exists():
        username = os.environ.get("HEISENBERG_USERNAME_OR_EMAIL", "")
        password = os.environ.get("HEISENBERG_PASSWORD", "")
        if not username or not password:
            print("ERROR: credentials not set in .env")
            return

        page.goto("https://heisenberg.kr/login/", wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        page.fill('input[data-key="username"]', username)
        page.fill('input[data-key="user_password"]', password)
        page.click("input#um-submit-btn")
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(3000)

        AUTH_STATE.parent.mkdir(parents=True, exist_ok=True)
        context.storage_state(path=str(AUTH_STATE))
        print(f"Auth state saved to {AUTH_STATE}")

    page.goto("https://heisenberg.kr/latest/", wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    print(f"Current URL: {page.url}")

    # Save HTML and screenshot
    html = page.content()
    html_path = OUT_DIR / "heisenberg_latest.html"
    with open(html_path, "w") as f:
        f.write(html)
    print(f"HTML saved to {html_path}")

    screenshot_path = OUT_DIR / "heisenberg_latest.png"
    page.screenshot(path=str(screenshot_path), full_page=True)
    print(f"Screenshot saved to {screenshot_path}")

    page.close()
    context.close()
    browser.close()
    pw.stop()


if __name__ == "__main__":
    main()

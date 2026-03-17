"""Capture a detail page after login — uses auth_state from prior capture."""

from pathlib import Path

from playwright.sync_api import sync_playwright

AUTH_STATE = Path("data/runtime/auth_state.json")


def main():
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)

    if AUTH_STATE.exists():
        context = browser.new_context(storage_state=str(AUTH_STATE))
        print(f"Loaded auth_state from {AUTH_STATE}")
    else:
        print("ERROR: no auth_state found. Run capture_latest.py first.")
        return

    page = context.new_page()

    # Go to the first article from latest (GTC2026)
    url = "https://heisenberg.kr/gtc2026/"
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_timeout(5000)

    print(f"Current URL: {page.url}")

    html = page.content()
    html_path = Path("/tmp/heisenberg_detail.html")
    with open(html_path, "w") as f:
        f.write(html)
    print(f"HTML saved to {html_path}")

    screenshot_path = Path("/tmp/heisenberg_detail.png")
    page.screenshot(path=str(screenshot_path), full_page=True)
    print(f"Screenshot saved to {screenshot_path}")

    page.close()
    context.close()
    browser.close()
    pw.stop()


if __name__ == "__main__":
    main()

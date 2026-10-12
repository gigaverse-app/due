"""Real browser acceptance for both storefronts; uses running benchmark servers."""

import argparse
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("arm", choices=("due", "dbos"))
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", type=Path, default=Path("test-results/widgets"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1100})
        page.goto(args.url)
        if args.arm == "due":
            page.get_by_role("button", name="Reserve a widget").click()
            row = page.locator(".order").first
            row.get_by_role("button", name="Pay (simulated)", exact=True).click()
            expect(row.locator("progress")).not_to_have_attribute("value", "0", timeout=15000)
            page.screenshot(path=str(args.output / "due-progress.png"), full_page=True)
            page.get_by_role("button", name="Crash & restart application").click()
            expect(row).to_contain_text("dispatched", timeout=65000)
            expect(row.locator("progress")).to_have_attribute("value", "10")
            page.screenshot(path=str(args.output / "due-recovered.png"), full_page=True)
            page.get_by_role("button", name="Reserve a widget").click()
            page.locator(".order").first.get_by_role("button", name="Fail payment").click()
            expect(page.locator(".order").first).to_contain_text("cancelled", timeout=15000)
        else:
            page.get_by_role("button", name="Buy Now").click()
            page.get_by_role("button", name="Confirm Payment").click()
            current = page.locator('[x-data="retrieveOrder(orderId)"]')
            expect(current.locator('[x-text="order.status"]')).to_have_text("PAID", timeout=15000)
            page.screenshot(path=str(args.output / "dbos-progress.png"), full_page=True)
            page.get_by_role("button", name="Crash the Application", exact=True).click()
            expect(current.locator('[x-text="order.status"]')).to_have_text("DISPATCHED", timeout=65000)
            page.screenshot(path=str(args.output / "dbos-recovered.png"), full_page=True)
            page.get_by_role("button", name="Continue Shopping").click()
            page.get_by_role("button", name="Buy Now").click()
            page.get_by_role("button", name="Cancel Payment").click()
            expect(current.locator('[x-text="order.status"]')).to_have_text("CANCELLED", timeout=15000)
        browser.close()
    print(f"{args.arm}: browser checkout, payment, crash and recovery passed")


if __name__ == "__main__":
    main()

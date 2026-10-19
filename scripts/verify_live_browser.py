"""Fresh server-side browser smoke check of the deployed workspace."""

import json
import os

from playwright.sync_api import sync_playwright

domain = os.environ["POLICY_DOMAIN"]
server_ip = os.environ["POLICY_SERVER_IP"]

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(
        headless=True,
        args=[f"--host-resolver-rules=MAP {domain} {server_ip}"],
    )
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="zh-CN")
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    response = page.goto(f"https://{domain}/", wait_until="networkidle", timeout=45000)
    page.locator('[data-workspace="radar"]').click()
    buttons = page.get_by_role("button", name="深入查找原文", exact=True)
    buttons.first.wait_for(timeout=15000)
    assert response.status == 200 and buttons.count() > 0 and not errors
    page.screenshot(path="/tmp/juzi-opencode-radar.png", full_page=False)
    print(
        json.dumps(
            {"http_status": response.status, "research_buttons": buttons.count(), "javascript_errors": errors}
        )
    )
    browser.close()

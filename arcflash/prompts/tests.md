Task: write Playwright tests for these scenarios: {{scenario_ids}}. The grader runs its own tests written from the same scenarios. Write yours the way it does, so that a failure here predicts a failure there.

- One test per scenario, titled "<scenario ID> <scenario name>". Tests are independent, run in parallel and share one database that already holds other tests' records.
- Start with await page.goto("/") unless the scenario names another URL. Then act like a user: click links and buttons and fill fields as the steps say. Never open a URL the requirements do not give.
- Find elements only with the helpers imported from "./helpers". Each takes (page or locator, exact name): link, button, action (a link or a button; use it whenever the requirements do not state the role), textbox, combobox, checkbox, radio, radiogroup, heading and region; dialog and alert take a name only when the requirements give one; text finds exact visible text. Scope a lookup to a region or dialog when the requirement names one. No CSS selectors, test ids or XPath.
- Records the Given says were made through the UI (UI-created, UI-published, registered) are made through the UI first, named uniqueName("<the name the scenario uses>"), and the test uses that returned name everywhere. Records the requirements say already exist are used with their exact values.
- Usernames, email addresses, phone numbers and other values with a fixed format are made with uniqueId("<base>"), which returns letters, digits and _ only, for example uniqueId("traveler") + "@example.com". Never put a uniqueName value into such a field.
- Assert what Then says with expect(...).toBeVisible(), .toHaveCount(n), .toHaveValue(...), .toHaveURL(...) or .toBeChecked(). When a scenario reloads, call page.reload() and assert again.
- When a scenario needs a browser session that matches no account, call invalidateSession(page) first.
- No waitForTimeout, test.skip, test.only or retries.

Output exactly one file block and nothing else:
=== FILE: tests/scenarios-{{n}}.spec.ts ===
import { test, expect, link, button, action, textbox, combobox, checkbox, radio, radiogroup, heading, region, dialog, alert, text, uniqueName, uniqueId, invalidateSession } from "./helpers";
(the tests)
=== END FILE ===

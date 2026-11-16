// Page check: every planned page without a :parameter opens directly, renders, and shows each fixed name at most once.
import fs from "node:fs";
import { test, expect } from "./helpers";

type Target = { route: string; controls: { role: string; name: string }[] };
const file = process.env.ARCFLASH_SMOKE;
const targets: Target[] = file && fs.existsSync(file) ? JSON.parse(fs.readFileSync(file, "utf8")) : [];

for (const target of targets) {
  test(`smoke ${target.route}`, async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(`page error: ${error.message}`));
    page.on("response", (response) => {
      if (response.url().includes("/api/") && response.status() >= 500) {
        errors.push(`${response.status()} from ${response.request().method()} ${new URL(response.url()).pathname}`);
      }
    });
    await page.goto(target.route);
    await page.waitForLoadState("networkidle");
    // the page's own errors first: a repair sees the first lines of this test's error (ArcZenith Keep lane: a white
    // page reported only "rendered nothing", while the cause was "ReferenceError: notification is not defined")
    expect.soft(errors, errors.join("\n")).toEqual([]);
    expect.soft(await page.locator("#root > *").count(), "the page rendered nothing inside #root").toBeGreaterThan(0);
    for (const control of target.controls) {
      const n = await page.getByRole(control.role as Parameters<typeof page.getByRole>[0], { name: control.name, exact: true }).count();
      expect.soft(n, `${n} elements with role ${control.role} and name "${control.name}" (at most one allowed)`).toBeLessThanOrEqual(1);
    }
  });
}

// Lookups in the grader's style (role + exact name), grader-shaped names, and failure context for repairs.
import { test as base, expect, type Locator, type Page } from "@playwright/test";
import { randomBytes } from "node:crypto";

type Surface = Page | Locator;

export const test = base.extend<{ failureContext: void }>({
  failureContext: [
    async ({ page }, use, testInfo) => {
      await use();
      if (testInfo.status !== testInfo.expectedStatus) {
        let aria = "";
        try {
          aria = await page.locator("body").ariaSnapshot({ timeout: 2000 });
        } catch {
          aria = "";
        }
        await testInfo.attach("failure-context", {
          body: JSON.stringify({ url: page.url(), aria }),
          contentType: "application/json",
        });
      }
    },
    { auto: true },
  ],
});

export { expect };

const byRole = (role: Parameters<Page["getByRole"]>[0]) => (surface: Surface, name: string) =>
  surface.getByRole(role, { name, exact: true });

export const link = byRole("link");
export const button = byRole("button");
export const textbox = byRole("textbox");
export const combobox = byRole("combobox");
export const checkbox = byRole("checkbox");
export const radio = byRole("radio");
export const radiogroup = byRole("radiogroup");
export const heading = byRole("heading");
export const region = byRole("region");

export function action(surface: Surface, name: string): Locator {
  return button(surface, name).or(link(surface, name));
}

export function dialog(surface: Surface, name?: string): Locator {
  return name === undefined ? surface.getByRole("dialog") : surface.getByRole("dialog", { name, exact: true });
}

export function alert(surface: Surface, name?: string): Locator {
  return name === undefined ? surface.getByRole("alert") : surface.getByRole("alert", { name, exact: true });
}

export function text(surface: Surface, value: string): Locator {
  return surface.getByText(value, { exact: true });
}

const hex = (bytes: number) => randomBytes(bytes).toString("hex");

export function uniqueName(baseName: string): string {
  const info = test.info();
  const slug = info.title.replace(/[^A-Za-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60).replace(/-+$/g, "");
  return `${baseName} [${hex(8)}-w${info.workerIndex}-${slug}]`;
}

/** Letters, digits and _ only: for usernames, emails and other values with a fixed format. */
export function uniqueId(baseName: string): string {
  const base = baseName.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "") || "id";
  return `${base}_${hex(5)}w${test.info().workerIndex}`;
}

export async function invalidateSession(page: Page): Promise<void> {
  const context = page.context();
  const cookies = await context.cookies();
  if (cookies.length === 0) return;
  await context.clearCookies();
  await context.addCookies(cookies.map((c) => ({ ...c, value: `invalid-${hex(8)}` })));
}

import { test, expect, link, button, action, textbox, combobox, checkbox, radio, radiogroup, heading, region, dialog, alert, text, uniqueName, uniqueId, invalidateSession } from "./helpers";

test("SCN-REQ-1.1-01 List a created note", async ({ page }) => {
  const title = uniqueName("Note 1.1");
  await page.goto("/");
  await link(page, "Notes").click();
  await action(page, "Create Note").click();
  await textbox(page, "Title").fill(title);
  await textbox(page, "Body").fill("Body 1.1");
  await action(page, "Save Note").click();
  await expect(heading(page, title)).toBeVisible();
  await link(page, "Notes").click();
  await expect(action(region(page, "Note list"), title)).toHaveCount(1);
});

test("SCN-REQ-1.2-01 Save a new note", async ({ page }) => {
  const title = uniqueName("Note 1.2");
  const body = uniqueId("body");
  await page.goto("/");
  await link(page, "Notes").click();
  await action(page, "Create Note").click();
  await textbox(page, "Title").fill(title);
  await textbox(page, "Body").fill(body);
  await action(page, "Save Note").click();
  await expect(heading(page, title)).toBeVisible();
  await expect(text(page, body)).toBeVisible();
  await page.reload();
  await expect(heading(page, title)).toBeVisible();
  await expect(text(page, body)).toBeVisible();
});

test("SCN-REQ-1.3-01 Confirm deletion", async ({ page }) => {
  const title = uniqueName("Note 1.3");
  await page.goto("/");
  await link(page, "Notes").click();
  await action(page, "Create Note").click();
  await textbox(page, "Title").fill(title);
  await textbox(page, "Body").fill("Body 1.3");
  await action(page, "Save Note").click();
  await expect(heading(page, title)).toBeVisible();
  await button(page, "Delete").click();
  await button(dialog(page), "Confirm Delete").click();
  await expect(region(page, "Note list")).toBeVisible();
  await expect(action(region(page, "Note list"), title)).toHaveCount(0);
});

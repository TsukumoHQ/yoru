import { test, expect } from "@playwright/test";
import { mockApi, SESSION_ID } from "./fixtures";

test.beforeEach(async ({ page }) => {
  await mockApi(page);
});

test("session detail renders hero, synthesis card, collapsed detail, cost chart, rail", async ({ page }) => {
  await page.goto(`/s/${SESSION_ID}`);

  await expect(page.locator("h1", { hasText: "Fix auth middleware bug" })).toBeVisible();

  const synthesis = page.locator('section[aria-label="Synthesis"]');
  await expect(synthesis).toBeVisible();
  await expect(synthesis.locator('section[aria-label="Session summary"]')).toContainText(
    "Fixed an off-by-one in the auth token expiry check",
  );
  await expect(synthesis.locator('section[aria-label="Session score"]')).toBeVisible();

  const costChart = page.locator('[aria-label^="Cumulative api-equivalent cost sparkline"]');
  await expect(costChart).toBeVisible();

  await expect(page.locator('aside[aria-label="Session rail"]')).toBeVisible();
  await expect(page.locator('section[aria-label="Files changed"]')).toBeVisible();

  const details = page.locator("details", {
    has: page.locator("summary", { hasText: "View full detail — causal replay, step-through, timeline" }),
  });
  await expect(details).toBeVisible();
  await expect(details).not.toHaveAttribute("open", "");
  await expect(page.locator('section[aria-label="Timeline"]')).toBeHidden();

  await details.locator("summary").click();
  await expect(details).toHaveAttribute("open", "");
  await expect(page.locator('section[aria-label="Timeline"]')).toBeVisible();
  await expect(page.getByRole("heading", { name: "Plain-English replay" })).toBeVisible();

  await page.screenshot({ path: "e2e/screenshots/session-detail.png", fullPage: true });
});

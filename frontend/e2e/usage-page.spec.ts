import { test, expect } from "@playwright/test";
import { mockApi } from "./fixtures";

test.beforeEach(async ({ page }) => {
  await mockApi(page);
});

test("usage page renders hero, chart, dev roster, spike callout, top spenders", async ({ page }) => {
  await page.goto("/settings/usage");

  await expect(page.locator("h1", { hasText: "Token usage" })).toBeVisible();
  await expect(page.getByText("spend, this period")).toBeVisible();

  const chart = page.locator('svg[role="img"][aria-label^="Cost trend"]');
  await expect(chart).toBeVisible();

  await expect(page.getByText("Devs (")).toBeVisible();

  const spike = page.locator('[role="alert"]', { hasText: "Spike ·" });
  await expect(spike.first()).toBeVisible();

  await expect(page.getByText("Top spenders", { exact: true })).toBeVisible();

  await page.screenshot({ path: "e2e/screenshots/usage-page.png", fullPage: true });
});

import { test, expect } from "@playwright/test";
import { mockApi } from "./fixtures";

const TOTAL = 60;

function rawSession(i: number) {
  return {
    id: `sess-${i}`,
    user: `user${i}@example.com`,
    started_at: "2026-08-21T10:00:00Z",
    ended_at: "2026-08-21T10:30:00Z",
    tools_count: 3,
    cost_usd: 1.23,
    tokens_input: 5000,
    tokens_output: 2000,
    flags: [],
    org_id: "org-1",
  };
}

test.beforeEach(async ({ page }) => {
  await mockApi(page);
  await page.route("**/api/v1/sessions?**", (route) => {
    const url = new URL(route.request().url());
    const limit = Number(url.searchParams.get("limit") ?? 50);
    const offset = Number(url.searchParams.get("offset") ?? 0);
    const items = Array.from({ length: TOTAL }, (_, i) => rawSession(i)).slice(offset, offset + limit);
    return route.fulfill({ json: { items, total: TOTAL, limit, offset } });
  });
});

test("sessions list paginates: page 1 then page 2", async ({ page }) => {
  await page.goto("/?flag=0");
  await expect(page.getByText("Page 1 of 2 · 60 sessions")).toBeVisible();
  await expect(page.getByText("user0@example.com")).toBeVisible();
  await page.screenshot({ path: "e2e/screenshots/sessions-page-1.png", fullPage: true });

  await page.getByRole("button", { name: "Next" }).click();
  await expect(page.getByText("Page 2 of 2 · 60 sessions")).toBeVisible();
  await expect(page.getByText("user59@example.com")).toBeVisible();
  await expect(page.getByRole("button", { name: "Next" })).toBeDisabled();
  await page.screenshot({ path: "e2e/screenshots/sessions-page-2.png", fullPage: true });
});

import { expect, test, type Page } from "@playwright/test";

async function ready(page: Page) {
  await page.goto("/");
  await page.getByLabel("Email").fill(process.env.E2E_EMAIL ?? "");
  await page.getByLabel("Password").fill(process.env.E2E_PASSWORD ?? "");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByText("One place for every supply question")).toBeVisible({ timeout: 150_000 });
}

const lastBot = (page: Page) => page.locator(".thread .msg.bot").last();

test("sign-in is required and wrong passwords are refused", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("Access is by invitation only")).toBeVisible();
  await page.getByLabel("Email").fill(process.env.E2E_EMAIL ?? "");
  await page.getByLabel("Password").fill("not-the-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByText("do not match an invited account")).toBeVisible();
});

test("boots the engine and shows the load state", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await ready(page);
  await expect(page.locator(".pill")).toContainText("Kinaxis snapshot 10/19/2026");
  await expect(page.getByText("None open")).toBeVisible();
  expect(errors).toEqual([]);
});

test("S1: Odor at Vegas asks the item and week, then shows the card", async ({ page }) => {
  await ready(page);
  await page.getByRole("button", { name: /\+5,000 cases of Odor at Vegas/ }).click();
  await expect(lastBot(page)).toContainText("more than one match");
  await lastBot(page).getByRole("button", { name: /^3283407/ }).click();
  await expect(lastBot(page)).toContainText("Which week");
  await lastBot(page).getByRole("button", { name: "Week of 10/19" }).click();
  const card = page.locator('[data-card="S1"]');
  await expect(card).toContainText("Do not support without a replan");
  await expect(card.locator("tr", { hasText: "USQ4" }).first()).toContainText("0.3");
  await expect(page.locator(".msg.answer").last()).toContainText("Every number checked");
  await card.getByRole("button", { name: "Open trace" }).click();
  await expect(page.getByRole("dialog")).toContainText("simulate_demand_change");
  await expect(page.getByRole("dialog")).toContainText("Numeric validator");
});

test("S3: capacity gap waits for planner input, then computes", async ({ page }) => {
  await ready(page);
  await page.getByRole("button", { name: /35% growth in 90 oz laundry/ }).click();
  const card = page.locator('[data-card="S3"]');
  await expect(card).toContainText("rated line capacity is missing");
  await expect(card).toContainText("2,144,804");
  await expect(page.locator('[data-form="rated_capacity"]')).toBeVisible();
  await page.getByLabel("Rated capacity, units per month").fill("2000000");
  await page.getByRole("button", { name: "Save and continue" }).click();
  await expect(card).toContainText("690,804");
  await expect(card).toContainText("planner-entered");
});

test("S4: decision tree asks pacing, proposes C0 and records the approval", async ({ page }) => {
  await ready(page);
  await page.getByRole("button", { name: /Code the 10\/15 Vegas cut/ }).click();
  const card = page.locator('[data-card="S4"]');
  await expect(card).toContainText("No code proposed yet");
  await lastBot(page).getByRole("button", { name: "No", exact: true }).click();
  await expect(lastBot(page)).toContainText("Was USQ4 (North Las Vegas) ordering above 130%");
  await lastBot(page).getByRole("button", { name: "No", exact: true }).click();
  await expect(card).toContainText("Proposed code C0");
  await lastBot(page).getByRole("button", { name: "Approve" }).click();
  await expect(lastBot(page)).toContainText("Approvals sheet");
  await expect(page.locator(".rail")).toContainText("1 this session");
});

test("Out of scope is refused with no card", async ({ page }) => {
  await ready(page);
  await page.getByRole("button", { name: /Upload a forecast to Kinaxis/ }).click();
  await expect(lastBot(page)).toContainText("I can only help with the five One Supply Bot scenarios");
  await expect(page.locator(".card")).toHaveCount(0);
});

test("Admin: golden checks all pass in the browser", async ({ page }) => {
  await ready(page);
  await page.getByRole("button", { name: "Admin" }).click();
  await page.getByRole("button", { name: "Golden checks" }).click();
  await page.getByRole("button", { name: "Run golden checks" }).click();
  await expect(page.getByText(/9 of 9 passed/)).toBeVisible({ timeout: 120_000 });
});

test("typed questions: PIR and S5", async ({ page }) => {
  await ready(page);
  await page.getByLabel("Message").fill("PIR for Lysol Sanitizers");
  await page.getByLabel("Message").press("Enter");
  await expect(page.locator('[data-card="PIR"]')).toContainText("Projected inventory report");
  await page.getByLabel("Message").fill("What changed since last week?");
  await page.getByLabel("Message").press("Enter");
  await lastBot(page).getByRole("button", { name: "Compare SAP stock between two dates" }).click();
  await expect(page.locator('[data-card="S5"]')).toContainText("Inventory fallback");
});

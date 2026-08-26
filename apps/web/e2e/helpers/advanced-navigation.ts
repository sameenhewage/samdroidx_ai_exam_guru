import { expect, type Page } from "@playwright/test";

export async function openAdvancedArea(page: Page, name: string) {
  const summary = page.locator("summary").filter({ hasText: /^Advanced$/ });
  await summary.click();
  const navigation = page.getByRole("navigation", { name: "Advanced admin navigation" });
  const link = navigation.getByRole("link", { name });
  await expect(link).toBeVisible();
  await link.click();
}

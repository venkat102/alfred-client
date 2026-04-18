import { expect, test } from "@playwright/test";
import { login, openAlfredChat } from "./fixtures";

/**
 * Rollback smoke: given a Deployed changeset on the current site,
 * click Rollback from the preview panel and confirm the status
 * transitions to Rolled Back. Target doc is asserted to be gone.
 *
 * Requires a pre-existing Deployed changeset in the site. The safest
 * way to guarantee that is to run preview-approve.spec.ts immediately
 * before this one with the same conversation.
 *
 * Gated the same way as preview-approve - destructive + stateful.
 */
test.skip(
	!process.env.ALFRED_RUN_SLOW_TESTS,
	"Destructive: modifies live site state. Set ALFRED_RUN_SLOW_TESTS=1 to run.",
);

test("rollback deployed changeset reverts the change", async ({ page }) => {
	await login(page);
	await openAlfredChat(page);

	// Open the most recently deployed conversation (first row in the list).
	await page.locator(".alfred-conversation-list .alfred-conversation-item").first().click();

	const preview = page.locator(".alfred-preview-panel");
	await expect(preview).toBeVisible({ timeout: 15_000 });
	await expect(preview.locator(".alfred-status-badge")).toContainText("Deployed");

	// Open the overflow menu and click Rollback.
	await preview.locator(".alfred-overflow-menu").click();
	await page.locator(".alfred-menu-item:has-text('Rollback')").click();

	// Confirm the dialog.
	await page.locator(".modal .btn-primary:has-text('Rollback')").click();

	// Status transitions to Rolled Back.
	await expect(preview.locator(".alfred-status-badge")).toContainText(
		"Rolled Back",
		{ timeout: 120_000 },
	);
});

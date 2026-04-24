import { expect, test } from "@playwright/test";
import {
	login,
	openAlfredChat,
	selectMode,
	startNewConversation,
} from "./fixtures";

/**
 * Mode switcher persists its selection to the backend via
 * set_conversation_mode, and the choice survives a page reload.
 *
 * This test lives here instead of in a pure backend test because the
 * switcher's UI state is where regressions show up - backend tests
 * already cover the API endpoint itself.
 */
test("mode switcher persists across page reload", async ({ page }) => {
	await login(page);
	await openAlfredChat(page);
	await startNewConversation(page);

	await selectMode(page, "plan");
	// Reload; the persisted conversation mode should rehydrate.
	await page.reload();
	await expect(page.locator(".alfred-conversation-list")).toBeVisible();
	// aria-pressed is the a11y-stable signal of which mode is active;
	// the CSS class used to encode this can change at any time.
	await expect(page.getByTestId("alfred-mode-plan")).toHaveAttribute(
		"aria-pressed", "true",
	);
});

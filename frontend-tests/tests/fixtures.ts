import { expect, Page } from "@playwright/test";

/**
 * Shared helpers used by every spec. Keep login, chat open, and
 * prompt send in one place so UI churn only breaks one file.
 *
 * Interactive elements use `data-testid` attributes (shipped in
 * alfred_processing commits 57ecc05 + da847f4). Class selectors are
 * retained only for structural containers (.alfred-conversation-list,
 * .alfred-preview-panel, .alfred-message) that are stable + not
 * interactive. See frontend-tests/README.md "Stable selectors" for
 * the full table.
 */

const USER = process.env.ALFRED_USER || "Administrator";
const PASSWORD = process.env.ALFRED_PASSWORD || "admin";

/** Log into Frappe via /login, then land on /app. */
export async function login(page: Page): Promise<void> {
	await page.goto("/login");
	// Frappe's login form uses #login_email + #login_password.
	await page.locator("#login_email").fill(USER);
	await page.locator("#login_password").fill(PASSWORD);
	await page.locator(".btn-login").click();
	await page.waitForURL(/\/app/);
}

/** Open the Alfred chat page and wait for the conversation list to render. */
export async function openAlfredChat(page: Page): Promise<void> {
	await page.goto("/app/alfred");
	// AlfredChatApp mounts under a div Frappe gives us; wait for the
	// conversation list to render as a readiness signal.
	await expect(page.locator(".alfred-conversation-list")).toBeVisible({
		timeout: 15_000,
	});
}

/**
 * Click the "New Conversation" button and wait for the empty chat view.
 * Returns once the input textarea is interactive.
 */
export async function startNewConversation(page: Page): Promise<void> {
	await page.getByTestId("alfred-new-conversation").click();
	await expect(page.getByTestId("alfred-composer-input")).toBeVisible();
}

/**
 * Type a prompt and hit Send. Does not wait for a reply - use
 * waitForAgentReply if you need that.
 */
export async function sendPrompt(page: Page, text: string): Promise<void> {
	const input = page.getByTestId("alfred-composer-input");
	await input.fill(text);
	await page.getByTestId("alfred-send-btn").click();
	// The user bubble should appear immediately (optimistic render).
	// The message bubbles don't have testids (they're rendered in a
	// v-for and per-message testids would be cluttered); we match on
	// the role-specific class that's stable across CSS refactors.
	await expect(
		page.locator(".alfred-message.alfred-msg-user").last(),
	).toContainText(text);
}

/**
 * Wait until the agent produces at least one non-typing message after
 * the user's last prompt. Timeouts are long because crews can take
 * several minutes on slow LLMs.
 */
export async function waitForAgentReply(page: Page, timeoutMs = 120_000): Promise<void> {
	await expect(
		page.locator(".alfred-message.alfred-msg-agent").last(),
	).toBeVisible({ timeout: timeoutMs });
}

/**
 * Pick a mode from the ModeSwitcher ("auto" | "dev" | "plan" | "insights").
 * The switcher persists the choice to the backend via set_conversation_mode.
 */
export async function selectMode(
	page: Page,
	mode: "auto" | "dev" | "plan" | "insights",
): Promise<void> {
	await page.getByTestId(`alfred-mode-${mode}`).click();
	// aria-pressed reflects the active state and is a11y-stable unlike
	// the .alfred-mode-btn-active CSS class name.
	await expect(page.getByTestId(`alfred-mode-${mode}`)).toHaveAttribute(
		"aria-pressed", "true",
	);
}

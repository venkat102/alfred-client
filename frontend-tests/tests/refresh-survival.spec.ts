import { expect, test, request as playwrightRequest } from "@playwright/test";
import { login, openAlfredChat } from "./fixtures";

/**
 * Refresh-survival: the preview panel must re-render the correct state
 * after a hard page reload, driven entirely by what the backend already
 * has in the DB (Alfred Conversation + Alfred Changeset). No LLM run
 * is required - we seed a pending / deployed / rolled-back changeset via
 * REST, then assert `data-preview-state` on `.alfred-preview` after the
 * chat loads.
 *
 * Runs only under ALFRED_RUN_SLOW_TESTS=1 because it mutates live DB
 * state. REST-only (no crew tokens spent) so it is cheap to re-run.
 */
test.skip(
	!process.env.ALFRED_RUN_SLOW_TESTS,
	"Stateful: writes Alfred Conversation + Alfred Changeset rows. Set ALFRED_RUN_SLOW_TESTS=1 to run.",
);

const USER = process.env.ALFRED_USER || "Administrator";
const PASSWORD = process.env.ALFRED_PASSWORD || "admin";

async function apiCreateConversation(baseURL: string, storageState: any): Promise<string> {
	// Use the whitelisted create_conversation method so permissions +
	// owner metadata match what the UI produces. Cheaper than calling
	// frappe.client.insert directly and closer to production behaviour.
	const ctx = await playwrightRequest.newContext({ baseURL, storageState });
	const res = await ctx.post(
		"/api/method/alfred_client.alfred_settings.page.alfred_chat.alfred_chat.create_conversation",
	);
	expect(res.ok()).toBeTruthy();
	const body = await res.json();
	return body.message.name as string;
}

async function apiSeedChangeset(
	baseURL: string,
	storageState: any,
	conversation: string,
	status: string,
	extras: Record<string, unknown> = {},
): Promise<string> {
	const ctx = await playwrightRequest.newContext({ baseURL, storageState });
	const res = await ctx.post("/api/method/frappe.client.insert", {
		form: {
			doc: JSON.stringify({
				doctype: "Alfred Changeset",
				conversation,
				status,
				changes: JSON.stringify([
					{ op: "create", doctype: "Notification", data: { name: "refresh-test" } },
				]),
				dry_run_valid: 1,
				dry_run_issues: "[]",
				...extras,
			}),
		},
	});
	expect(res.ok()).toBeTruthy();
	const body = await res.json();
	return body.message.name as string;
}

test.describe("refresh-survival: preview panel rehydrates from the DB", () => {
	test("Pending changeset renders as PENDING state after reload", async ({ page, baseURL }) => {
		await login(page);
		const storage = await page.context().storageState();
		const conv = await apiCreateConversation(baseURL!, storage);
		await apiSeedChangeset(baseURL!, storage, conv, "Pending");

		// Navigate directly to the conversation route so the UI does a full
		// rehydrate through openConversation + get_conversation_state.
		await page.goto(`/app/alfred/${conv}`);
		const preview = page.locator(".alfred-preview");
		await expect(preview).toBeVisible({ timeout: 15_000 });
		await expect(preview).toHaveAttribute("data-preview-state", "PENDING");

		// Approve + Reject buttons must be present.
		await expect(preview.locator(".alfred-preview-actions")).toBeVisible();
	});

	test("Deployed changeset renders as DEPLOYED read-only on reload", async ({ page, baseURL }) => {
		await login(page);
		const storage = await page.context().storageState();
		const conv = await apiCreateConversation(baseURL!, storage);
		await apiSeedChangeset(baseURL!, storage, conv, "Deployed", {
			rollback_data: JSON.stringify([
				{ op: "delete", doctype: "Notification", name: "refresh-test" },
			]),
			deployment_log: JSON.stringify([
				{ step: 1, doctype: "Notification", name: "refresh-test", status: "success" },
			]),
		});

		await page.goto(`/app/alfred/${conv}`);
		const preview = page.locator(".alfred-preview");
		await expect(preview).toBeVisible({ timeout: 15_000 });
		await expect(preview).toHaveAttribute("data-preview-state", "DEPLOYED");

		// Rollback button visible because rollback_data is populated.
		await expect(preview.locator(".alfred-preview-actions button")).toContainText("Rollback");
	});

	test("Rolled Back changeset renders as ROLLED_BACK after reload", async ({ page, baseURL }) => {
		await login(page);
		const storage = await page.context().storageState();
		const conv = await apiCreateConversation(baseURL!, storage);
		// User-initiated rollback: deployment_log has success entries then
		// rollback entries (no failed step).
		await apiSeedChangeset(baseURL!, storage, conv, "Rolled Back", {
			deployment_log: JSON.stringify([
				{ step: 1, doctype: "Notification", name: "refresh-test", status: "success" },
				{ op: "rollback", doctype: "Notification", name: "refresh-test", status: "success" },
			]),
		});

		await page.goto(`/app/alfred/${conv}`);
		const preview = page.locator(".alfred-preview");
		await expect(preview).toBeVisible({ timeout: 15_000 });
		await expect(preview).toHaveAttribute("data-preview-state", "ROLLED_BACK");
	});

	test("Failed mid-deploy changeset renders as FAILED after reload", async ({ page, baseURL }) => {
		await login(page);
		const storage = await page.context().storageState();
		const conv = await apiCreateConversation(baseURL!, storage);
		// Auto-rollback from deploy failure: a `failed` entry exists in the log.
		await apiSeedChangeset(baseURL!, storage, conv, "Rolled Back", {
			deployment_log: JSON.stringify([
				{ step: 1, doctype: "Notification", name: "refresh-test", status: "success" },
				{ step: 2, doctype: "Workflow", name: "broken", status: "failed", error: "target doctype missing" },
				{ op: "rollback", doctype: "Notification", name: "refresh-test", status: "success" },
			]),
		});

		await page.goto(`/app/alfred/${conv}`);
		const preview = page.locator(".alfred-preview");
		await expect(preview).toBeVisible({ timeout: 15_000 });
		await expect(preview).toHaveAttribute("data-preview-state", "FAILED");
	});

	test("Empty conversation renders as EMPTY", async ({ page, baseURL }) => {
		await login(page);
		const storage = await page.context().storageState();
		const conv = await apiCreateConversation(baseURL!, storage);

		await page.goto(`/app/alfred/${conv}`);
		const preview = page.locator(".alfred-preview");
		await expect(preview).toBeVisible({ timeout: 15_000 });
		await expect(preview).toHaveAttribute("data-preview-state", "EMPTY");
	});
});

/**
 * Preview-panel actions: approve / modify / reject / rollback.
 *
 * The preview drawer exposes four buttons; each maps to one of these
 * functions. This composable owns the frappe.confirm + frappe.call +
 * frappe.msgprint + frappe.show_alert flows so AlfredChatApp.vue only
 * has to wire them to the template.
 *
 * Required refs (all reactive):
 *   - changeset            current changeset; mutated on status transitions
 *   - validatingChangeset  true while Approve's dry-run is in flight
 *   - rollbackInFlight     true while Rollback's call is in flight
 *   - isDeployed           drives the DEPLOYED preview-panel state
 *   - conversationStatus   reset to "Completed" after a successful rollback
 *   - messages             system-status rows pushed on terminal states
 *   - inputDisabled        cleared by startModify so the user can type
 *   - inputPlaceholder     set by startModify to the "what to change" prompt
 *   - inputField           template ref for the textarea; focused by startModify
 *
 * Required functions:
 *   - addActivity(text, level?)  live activity-ticker feed
 *   - nextTick                    vue's nextTick (so the composable stays
 *                                 independent of the vue import in the SFC)
 *
 * All four actions are user-initiated from the preview panel. None of
 * them open/close the drawer themselves; useDrawerState owns that. None
 * of them touch isProcessing or stopTimer either - those belong to the
 * realtime event layer that will be extracted next.
 */

export function usePreviewActions({
	changeset,
	validatingChangeset,
	rollbackInFlight,
	isDeployed,
	conversationStatus,
	messages,
	inputDisabled,
	inputPlaceholder,
	inputField,
	addActivity,
	nextTick,
}) {
	function approveChangeset() {
		if (!changeset.value) return;
		const changes = changeset.value.changes || [];

		frappe.confirm(
			`<p><strong>${__("Deploy to your live site?")}</strong></p>
			 <p class="text-muted">${__("A dry-run validation will be performed first. Changes:")}</p>
			 <ul style="text-align:left">${changes.map((c) =>
				`<li><strong>${c.op || c.operation || "create"}</strong> ${c.doctype}: ${frappe.utils.escape_html((c.data || {}).name || "Unnamed")}</li>`
			).join("")}</ul>`,
			() => {
				validatingChangeset.value = true;
				frappe.show_alert({ message: __("Validating changeset..."), indicator: "blue" });
				frappe.call({
					method: "alfred_client.alfred_settings.page.alfred_chat.alfred_chat.approve_changeset",
					args: { changeset_name: changeset.value.name },
					callback: (r) => {
						validatingChangeset.value = false;
						if (!r.message) return;
						let result = r.message;

						if (result.status === "validation_failed") {
							// Dry-run failed - show issues without deploying
							let issueHtml = (result.issues || []).map((i) =>
								`<li><strong>[${i.severity}]</strong> Step ${i.step} (${i.doctype}): ${frappe.utils.escape_html(i.issue)}</li>`
							).join("");
							frappe.msgprint({
								title: __("Dry-Run Validation Failed"),
								indicator: "red",
								message: `<p>${result.message}</p><ul>${issueHtml}</ul>
									<p class="text-muted">${__("No changes were made to your site. Fix the issues and try again.")}</p>`,
							});
							messages.value.push({
								_id: Date.now(), role: "system", message_type: "error",
								content: `Deployment validation failed: ${result.message}`,
							});
						} else if (result.status === "success") {
							isDeployed.value = true;
							if (changeset.value) changeset.value.status = "Deployed";
							frappe.show_alert({ message: __("Deployment complete!"), indicator: "green" });
						} else if (result.status === "failed") {
							if (changeset.value) changeset.value.status = "Rolled Back";
							frappe.msgprint({
								title: __("Deployment Failed"),
								indicator: "red",
								message: result.error || "An error occurred during deployment.",
							});
						}
					},
					error: () => {
						validatingChangeset.value = false;
						frappe.show_alert({ message: __("Deployment failed."), indicator: "red" });
					},
				});
			}
		);
	}

	function startModify() {
		inputDisabled.value = false;
		inputPlaceholder.value = __("What would you like to change?");
		nextTick(() => inputField.value?.focus());
	}

	function rejectChangeset() {
		if (!changeset.value) return;
		frappe.confirm(__("Are you sure you want to reject this changeset?"), () => {
			frappe.call({
				method: "alfred_client.alfred_settings.page.alfred_chat.alfred_chat.reject_changeset",
				args: { changeset_name: changeset.value.name },
				callback: () => {
					if (changeset.value) changeset.value.status = "Rejected";
					frappe.show_alert({ message: __("Changeset rejected."), indicator: "orange" });
				},
			});
		});
	}

	function rollbackChangeset() {
		// Triggered by the Rollback button on the DEPLOYED preview panel.
		// Unlike the automatic rollback on deploy failure, this is a user
		// action on a successfully-deployed changeset.
		const cs = changeset.value;
		if (!cs || !cs.name) return;
		if (rollbackInFlight.value) return;
		frappe.confirm(
			__("Rollback deploys all removed-record data back to Alfred but will DELETE every document that was created. Continue?"),
			() => {
				rollbackInFlight.value = true;
				addActivity(__("Rolling back deploy..."));
				frappe.call({
					method: "alfred_client.api.deploy.rollback_changeset",
					args: { changeset_name: cs.name },
					callback: (r) => {
						rollbackInFlight.value = false;
						const result = (r && r.message) || {};
						const status = result.status || "";
						if (status === "Rolled Back") {
							changeset.value = {
								...cs,
								status: "Rolled Back",
								deployment_log: result.deployment_log || cs.deployment_log,
							};
							isDeployed.value = false;
							conversationStatus.value = "Completed";
							addActivity(__("Rollback complete."));
							messages.value.push({
								_id: Date.now(),
								role: "system",
								message_type: "status",
								content: __("Deploy rolled back."),
							});
						} else {
							addActivity(__("Rollback reported status: {0}", [status || "unknown"]), "error");
						}
					},
					error: (err) => {
						rollbackInFlight.value = false;
						addActivity(__("Rollback failed"), "error");
						console.warn("Rollback failed:", err);
					},
				});
			},
		);
	}

	return {
		approveChangeset,
		startModify,
		rejectChangeset,
		rollbackChangeset,
	};
}

/**
 * Conversation-admin helpers: load / delete / share / health.
 *
 * These are the five functions that only ever read/write the
 * conversations list ref or the currentConversation ref, so they're
 * the cleanest slice to extract. newConversation / openConversation /
 * goBack / deleteConversation stay in AlfredChatApp.vue because they
 * mutate ~15 refs during a conversation switch and belong to the
 * main chat state lifecycle.
 *
 * Required refs:
 *   - conversations: ref<Array>          populated by loadConversations
 *   - currentConversation: ref<string>   used only by checkHealth to
 *                                        gate the health-dialog call
 *
 * All backend endpoints are called via frappe.call; all user-visible
 * output goes through frappe.show_alert / frappe.confirm / frappe.ui.Dialog
 * / frappe.msgprint. The composable assumes those frappe globals exist
 * (they are provided by the Desk shell).
 */

export function useConversationAdmin({ conversations, currentConversation }) {
	function loadConversations() {
		frappe.call({
			method: "alfred_client.alfred_settings.page.alfred_chat.alfred_chat.get_conversations",
			callback: (r) => { if (r.message) conversations.value = r.message; },
		});
	}

	function confirmAndDelete(conversation, onSuccess) {
		frappe.confirm(
			__("Delete this conversation and all its messages? This cannot be undone."),
			() => {
				frappe.call({
					method: "alfred_client.alfred_settings.page.alfred_chat.alfred_chat.delete_conversation",
					args: { conversation },
					callback: () => {
						frappe.show_alert({ message: __("Conversation deleted"), indicator: "green" });
						onSuccess();
					},
					error: () => {
						frappe.show_alert({ message: __("Failed to delete conversation"), indicator: "red" });
					},
				});
			}
		);
	}

	function deleteConversationFromList(name) {
		confirmAndDelete(name, () => loadConversations());
	}

	function shareConversation(name) {
		const d = new frappe.ui.Dialog({
			title: __("Share Conversation"),
			fields: [
				{
					fieldname: "user",
					fieldtype: "Link",
					options: "User",
					label: __("User"),
					reqd: 1,
					filters: { enabled: 1, name: ["!=", frappe.session.user] },
				},
				{
					fieldname: "write",
					fieldtype: "Check",
					label: __("Allow writing (send messages)"),
					default: 0,
				},
			],
			primary_action_label: __("Share"),
			primary_action(values) {
				frappe.call({
					method: "alfred_client.alfred_settings.page.alfred_chat.alfred_chat.share_conversation",
					args: { conversation: name, user: values.user, write: values.write },
					callback: () => {
						frappe.show_alert({ message: __("Conversation shared with {0}", [values.user]), indicator: "green" });
						d.hide();
					},
					error: () => {
						frappe.show_alert({ message: __("Failed to share conversation"), indicator: "red" });
					},
				});
			},
		});
		d.show();
	}

	function checkHealth() {
		if (!currentConversation.value) return;
		frappe.call({
			method: "alfred_client.alfred_settings.page.alfred_chat.alfred_chat.get_conversation_health",
			args: { conversation: currentConversation.value },
			callback: (r) => {
				if (!r.message) return;
				const h = r.message;
				const esc = frappe.utils.escape_html;

				const lastMsg = h.last_message
					? `${esc(h.last_message.role)} (${esc(h.last_message.message_type)}) - ${esc(h.last_message.creation)}`
					: '<span class="text-muted">-</span>';

				const procStatus = h.processing_app_reachable
					? '<span style="color: var(--green-600); font-weight: 600;">&#10003; reachable</span>'
					: `<span style="color: var(--red-600); font-weight: 600;">&#10007; ${esc(h.processing_app_error || "unreachable")}</span>`;

				const jobStatus = h.background_job_running
					? '<span style="color: var(--green-600); font-weight: 600;">&#10003; running</span>'
					: '<span style="color: var(--red-600); font-weight: 600;">&#10007; not running</span>';

				const workerCount = Number.isFinite(h.long_worker_count) ? h.long_worker_count : -1;
				let workerStatus;
				if (workerCount === -1) {
					workerStatus = '<span class="text-muted">-</span>';
				} else if (workerCount === 0) {
					workerStatus = `<span style="color: var(--red-600); font-weight: 600;">&#10007; 0 workers</span>
						<span class="text-muted" style="margin-left: 8px;">${__("worker_long not running - check Procfile + bench restart")}</span>`;
				} else {
					workerStatus = `<span style="color: var(--green-600); font-weight: 600;">&#10003; ${workerCount} worker(s)</span>`;
				}

				const depth = h.redis_queue_depth || 0;
				const queueColor = depth === 0 ? "var(--green-600)" : "var(--orange-600)";
				const queueLabel = depth === 0
					? __("empty (drained or never had a message)")
					: __("{0} message(s) waiting", [depth]);

				const overallOk = h.processing_app_reachable && h.background_job_running && workerCount > 0;

				frappe.msgprint({
					title: __("Conversation Health"),
					indicator: overallOk ? "green" : "orange",
					message: `
						<table class="table table-bordered" style="margin: 0;">
							<tbody>
								<tr>
									<td style="width: 40%;"><strong>${__("Conversation Status")}</strong></td>
									<td>${esc(h.conversation_status || "-")}</td>
								</tr>
								<tr>
									<td><strong>${__("Current Agent")}</strong></td>
									<td>${esc(h.current_agent || "-")}</td>
								</tr>
								<tr>
									<td><strong>${__("Last Message")}</strong></td>
									<td>${lastMsg}</td>
								</tr>
								<tr>
									<td><strong>${__("Long-Queue Workers")}</strong></td>
									<td>${workerStatus}</td>
								</tr>
								<tr>
									<td><strong>${__("Background Job")}</strong></td>
									<td>${jobStatus}</td>
								</tr>
								<tr>
									<td><strong>${__("Redis Queue Depth")}</strong></td>
									<td>
										<span style="color: ${queueColor}; font-weight: 600;">${depth}</span>
										<span class="text-muted" style="margin-left: 8px;">${queueLabel}</span>
									</td>
								</tr>
								<tr>
									<td><strong>${__("Processing App")}</strong></td>
									<td>${procStatus}</td>
								</tr>
							</tbody>
						</table>
						<p class="text-muted text-xs" style="margin-top: 10px; margin-bottom: 0;">
							${__("Tip: send a prompt and click Health immediately. Queue depth should briefly show 1, then drop to 0 within 1-2 seconds as the connection manager drains it.")}
						</p>
					`,
				});
			},
			error: () => {
				frappe.show_alert({ message: __("Failed to fetch health"), indicator: "red" });
			},
		});
	}

	return {
		loadConversations,
		confirmAndDelete,
		deleteConversationFromList,
		shareConversation,
		checkHealth,
	};
}

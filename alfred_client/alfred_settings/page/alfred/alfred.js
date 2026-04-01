/**
 * Alfred Chat Page — Main entry point
 *
 * Two-panel layout: Chat (left 40%) | Preview (right 60%)
 * Real-time updates via Frappe Socket.IO
 * Access control via validate_alfred_access()
 */

frappe.pages["alfred"].on_page_load = function (wrapper) {
	var page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Alfred"),
		single_column: true,
	});

	page.main.addClass("alfred-page");
	$('<div class="alfred-container"></div>').appendTo(page.main);

	// Client-side access control — check BEFORE rendering any UI
	frappe.call({
		method: "alfred_client.api.permissions.has_app_permission",
		async: false,
		callback: function (r) {
			if (!r.message) {
				page.main.find(".alfred-container").html(
					`<div class="text-center" style="padding: 80px 20px;">
						<h4>${__("Not Authorized")}</h4>
						<p class="text-muted">${__("You do not have permission to access Alfred. Contact your administrator to request access.")}</p>
					</div>`
				);
				return;
			}
			page.alfred = new AlfredChat(page);
			page.alfred.init();
		},
	});
};

frappe.pages["alfred"].on_page_show = function (wrapper) {
	if (wrapper.page && wrapper.page.alfred) {
		wrapper.page.alfred.refresh();
	}
};

// ── Main Controller ─────────────────────────────────────────────

class AlfredChat {
	constructor(page) {
		this.page = page;
		this.container = page.main.find(".alfred-container");
		this.current_conversation = null;
		this.messages = [];
		this.connection_status = "disconnected";
		this.current_agent = null;
		this.current_phase = null;
		this.changeset_data = null;
	}

	init() {
		this.render_layout();
		this.setup_realtime();
		this.load_conversations();
		this.page.set_secondary_action(__("New Conversation"), () => this.new_conversation());
	}

	refresh() {
		this.load_conversations();
	}

	// ── Layout ──────────────────────────────────────────────────

	render_layout() {
		this.container.html(`
			<div class="alfred-main">
				<!-- Status Bar -->
				<div class="alfred-status-bar">
					<div class="alfred-agent-status">
						<span class="alfred-status-dot alfred-dot-disconnected"></span>
						<span class="alfred-status-text">${__("Ready")}</span>
					</div>
					<div class="alfred-phase-pipeline">
						${this.render_pipeline()}
					</div>
				</div>

				<!-- Two-panel layout -->
				<div class="alfred-panels">
					<!-- Left: Conversation list + Chat -->
					<div class="alfred-left-panel">
						<div class="alfred-conversation-list"></div>
						<div class="alfred-chat-area" style="display:none;">
							<div class="alfred-messages"></div>
							<div class="alfred-input-area">
								<textarea class="alfred-input" placeholder="${__("Type your message...")}" rows="2"></textarea>
								<button class="btn btn-primary btn-sm alfred-send-btn">${__("Send")}</button>
							</div>
						</div>
					</div>

					<!-- Right: Preview -->
					<div class="alfred-right-panel">
						<div class="alfred-preview">
							<div class="alfred-preview-empty">
								<div class="text-muted text-center" style="padding: 60px 20px;">
									<i class="fa fa-eye" style="font-size: 48px; margin-bottom: 16px; display: block; opacity: 0.3;"></i>
									<h5>${__("Preview Panel")}</h5>
									<p>${__("Changes proposed by Alfred will appear here for your review.")}</p>
								</div>
							</div>
							<div class="alfred-preview-content" style="display:none;"></div>
							<div class="alfred-preview-actions" style="display:none;">
								<button class="btn btn-success btn-sm alfred-approve-btn">${__("Approve & Deploy")}</button>
								<button class="btn btn-default btn-sm alfred-modify-btn">${__("Request Changes")}</button>
								<button class="btn btn-danger btn-sm alfred-reject-btn">${__("Reject")}</button>
							</div>
						</div>
					</div>
				</div>
			</div>
		`);

		this.setup_event_handlers();
	}

	render_pipeline() {
		const phases = [
			{ key: "requirement", label: "Req", icon: "📋" },
			{ key: "assessment", label: "Check", icon: "🔒" },
			{ key: "architecture", label: "Design", icon: "📐" },
			{ key: "development", label: "Build", icon: "⚙" },
			{ key: "testing", label: "Test", icon: "✓" },
			{ key: "deployment", label: "Deploy", icon: "🚀" },
		];

		return phases
			.map(
				(p) => `<span class="alfred-phase" data-phase="${p.key}">
				<span class="alfred-phase-icon">${p.icon}</span>
				<span class="alfred-phase-label">${p.label}</span>
			</span>`
			)
			.join('<span class="alfred-phase-arrow">→</span>');
	}

	// ── Event Handlers ──────────────────────────────────────────

	setup_event_handlers() {
		const me = this;

		// Send message
		this.container.find(".alfred-send-btn").on("click", () => me.send_message());
		this.container.find(".alfred-input").on("keydown", function (e) {
			if (e.key === "Enter" && !e.shiftKey) {
				e.preventDefault();
				me.send_message();
			}
		});

		// Preview actions
		this.container.find(".alfred-approve-btn").on("click", () => me.approve());
		this.container.find(".alfred-modify-btn").on("click", () => me.modify());
		this.container.find(".alfred-reject-btn").on("click", () => me.reject());
	}

	// ── Conversations ───────────────────────────────────────────

	load_conversations() {
		frappe.call({
			method: "alfred_client.alfred_settings.page.alfred.alfred.get_conversations",
			callback: (r) => {
				if (r.message) {
					this.render_conversation_list(r.message);
				}
			},
			error: () => {
				this.container
					.find(".alfred-conversation-list")
					.html(
						`<div class="text-center text-muted" style="padding: 40px;">
						<p>${__("Unable to load conversations.")}</p>
					</div>`
					);
			},
		});
	}

	render_conversation_list(conversations) {
		const list = this.container.find(".alfred-conversation-list");

		if (!conversations.length) {
			list.html(`
				<div class="text-center" style="padding: 40px;">
					<p class="text-muted">${__("No conversations yet.")}</p>
					<p class="text-muted">${__("Click 'New Conversation' to start.")}</p>
				</div>
			`);
			return;
		}

		const status_colors = {
			Open: "blue",
			"In Progress": "orange",
			"Awaiting Input": "yellow",
			Completed: "green",
			Escalated: "red",
			Failed: "red",
			Stale: "gray",
		};

		let html = '<div class="alfred-conv-items">';
		conversations.forEach((conv) => {
			const color = status_colors[conv.status] || "gray";
			const time = frappe.datetime.prettyDate(conv.modified);
			const agent = conv.current_agent ? ` — ${conv.current_agent}` : "";
			html += `
				<div class="alfred-conv-item" data-name="${conv.name}">
					<div class="alfred-conv-header">
						<span class="indicator-pill ${color}">${conv.status}</span>
						<span class="text-muted text-xs">${time}</span>
					</div>
					<div class="alfred-conv-name text-sm">${conv.name}${agent}</div>
				</div>
			`;
		});
		html += "</div>";
		list.html(html);

		// Click handler
		list.find(".alfred-conv-item").on("click", (e) => {
			const name = $(e.currentTarget).data("name");
			this.open_conversation(name);
		});
	}

	new_conversation() {
		frappe.call({
			method: "alfred_client.alfred_settings.page.alfred.alfred.create_conversation",
			callback: (r) => {
				if (r.message) {
					this.open_conversation(r.message.name);
					this.load_conversations();
				}
			},
		});
	}

	open_conversation(name) {
		this.current_conversation = name;
		this.messages = [];
		this.changeset_data = null;

		this.container.find(".alfred-conversation-list").hide();
		this.container.find(".alfred-chat-area").show();
		this.container.find(".alfred-messages").empty();
		this.container.find(".alfred-input").val("").prop("disabled", false);

		// Reset preview
		this.container.find(".alfred-preview-empty").show();
		this.container.find(".alfred-preview-content").hide().empty();
		this.container.find(".alfred-preview-actions").hide();

		// Load existing messages
		frappe.call({
			method: "alfred_client.alfred_settings.page.alfred.alfred.get_messages",
			args: { conversation: name },
			callback: (r) => {
				if (r.message) {
					r.message.forEach((msg) => this.append_message(msg));
					this.scroll_to_bottom();
				}
			},
		});

		// Add back button
		this.page.set_title(__("Alfred — {0}", [name]));
		this.page.set_primary_action(__("Back"), () => {
			this.container.find(".alfred-conversation-list").show();
			this.container.find(".alfred-chat-area").hide();
			this.page.set_title(__("Alfred"));
			this.page.clear_primary_action();
			this.current_conversation = null;
			this.load_conversations();
		});
	}

	// ── Messages ────────────────────────────────────────────────

	append_message(msg) {
		const area = this.container.find(".alfred-messages");
		const role_class = `alfred-msg-${msg.role || "system"}`;
		const type_class = `alfred-msg-type-${msg.message_type || "text"}`;
		const agent_badge = msg.agent_name
			? `<span class="alfred-agent-badge">${frappe.utils.escape_html(msg.agent_name)}</span>`
			: "";
		const time = msg.creation ? frappe.datetime.prettyDate(msg.creation) : "";

		let content = "";
		const safe_content = frappe.utils.escape_html(msg.content || "");

		switch (msg.message_type) {
			case "question":
				content = this.render_question(msg);
				break;
			case "status":
				content = `<div class="alfred-status-msg">${safe_content}</div>`;
				break;
			case "error":
				content = `<div class="alfred-error-msg">${safe_content}</div>`;
				break;
			case "changeset":
				content = `<div class="alfred-changeset-msg">${safe_content}</div>`;
				this.load_changeset_preview(msg.metadata);
				break;
			case "preview":
				content = `<div class="alfred-preview-msg">${safe_content}</div>`;
				break;
			default:
				// Render markdown for agent/text messages (safe — input is already escaped)
				content = `<div class="alfred-text-msg">${this.render_markdown(safe_content)}</div>`;
		}

		area.append(`
			<div class="alfred-message ${role_class} ${type_class}">
				<div class="alfred-msg-header">
					${agent_badge}
					<span class="alfred-msg-time text-muted text-xs">${time}</span>
				</div>
				<div class="alfred-msg-content">${content}</div>
			</div>
		`);
	}

	render_markdown(escaped_text) {
		/**
		 * Safe markdown renderer. Input is ALREADY HTML-escaped, so we convert
		 * markdown syntax to HTML without risking XSS.
		 *
		 * Supports: code blocks, inline code, bold, italic, links, lists, line breaks.
		 */
		let text = escaped_text;

		// Fenced code blocks: ```lang\ncode\n``` → <pre><code>
		text = text.replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) => {
			return `<pre class="alfred-code-preview"><code>${code.trim()}</code></pre>`;
		});

		// Inline code: `code` → <code>
		text = text.replace(/`([^`]+)`/g, "<code>$1</code>");

		// Bold: **text** or __text__
		text = text.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
		text = text.replace(/__(.+?)__/g, "<strong>$1</strong>");

		// Italic: *text* or _text_
		text = text.replace(/\*(.+?)\*/g, "<em>$1</em>");

		// Unordered list items: - item or * item (at line start)
		text = text.replace(/^[\-\*]\s+(.+)$/gm, "<li>$1</li>");
		text = text.replace(/(<li>.*<\/li>\n?)+/g, "<ul>$&</ul>");

		// Ordered list items: 1. item
		text = text.replace(/^\d+\.\s+(.+)$/gm, "<li>$1</li>");

		// Headers: ### text
		text = text.replace(/^###\s+(.+)$/gm, "<h6>$1</h6>");
		text = text.replace(/^##\s+(.+)$/gm, "<h5>$1</h5>");
		text = text.replace(/^#\s+(.+)$/gm, "<h4>$1</h4>");

		// Line breaks (not inside code blocks)
		text = text.replace(/\n/g, "<br>");

		// Clean up double <br> after block elements
		text = text.replace(/<\/(pre|ul|li|h[456])><br>/g, "</$1>");

		return text;
	}

	render_question(msg) {
		let html = `<div class="alfred-question">${frappe.utils.escape_html(msg.content || "")}</div>`;

		try {
			const meta = typeof msg.metadata === "string" ? JSON.parse(msg.metadata) : msg.metadata;
			if (meta && meta.options && Array.isArray(meta.options)) {
				html += '<div class="alfred-question-options">';
				meta.options.forEach((opt) => {
					html += `<button class="btn btn-xs btn-default alfred-option-btn" data-value="${frappe.utils.escape_html(opt)}">${frappe.utils.escape_html(opt)}</button>`;
				});
				html += "</div>";
			}
		} catch (e) {
			// Invalid metadata, show question only
		}

		return html;
	}

	send_message() {
		const input = this.container.find(".alfred-input");
		const message = input.val().trim();
		if (!message || !this.current_conversation) return;

		input.val("");

		// Optimistic UI: show message immediately
		this.append_message({
			role: "user",
			message_type: "text",
			content: message,
			creation: frappe.datetime.now_datetime(),
		});
		this.scroll_to_bottom();

		// Disable input during processing
		input.prop("disabled", true);
		this.container.find(".alfred-send-btn").prop("disabled", true);
		this.update_status("Processing...", "processing");

		frappe.call({
			method: "alfred_client.alfred_settings.page.alfred.alfred.send_message",
			args: { conversation: this.current_conversation, message: message },
			callback: () => {
				// Input stays disabled until agent responds
			},
			error: () => {
				input.prop("disabled", false);
				this.container.find(".alfred-send-btn").prop("disabled", false);
				this.update_status("Error sending message", "error");
			},
		});
	}

	scroll_to_bottom() {
		const area = this.container.find(".alfred-messages");
		area.scrollTop(area[0].scrollHeight);
	}

	// ── Preview Panel ───────────────────────────────────────────

	load_changeset_preview(metadata) {
		try {
			const data = typeof metadata === "string" ? JSON.parse(metadata) : metadata;
			if (!data || !data.changeset_name) return;

			frappe.call({
				method: "alfred_client.alfred_settings.page.alfred.alfred.get_changeset",
				args: { changeset_name: data.changeset_name },
				callback: (r) => {
					if (r.message) {
						this.changeset_data = r.message;
						this.render_preview(r.message);
					}
				},
			});
		} catch (e) {
			// Invalid metadata
		}
	}

	render_preview(changeset) {
		const preview = this.container.find(".alfred-preview-content");
		const empty = this.container.find(".alfred-preview-empty");
		const actions = this.container.find(".alfred-preview-actions");

		empty.hide();
		preview.show().empty();

		let html = `<h5 class="alfred-preview-title">${__("Changeset Preview")}</h5>`;

		if (!changeset.changes || !changeset.changes.length) {
			html += `<p class="text-muted">${__("No changes in this changeset.")}</p>`;
		} else {
			// Group changes by type
			const groups = {};
			changeset.changes.forEach((change) => {
				const dt = change.doctype || change.data?.doctype || "Other";
				if (!groups[dt]) groups[dt] = [];
				groups[dt].push(change);
			});

			Object.entries(groups).forEach(([type, items]) => {
				html += `<div class="alfred-preview-group">
					<h6 class="alfred-preview-group-title">${frappe.utils.escape_html(type)}s (${items.length})</h6>`;

				items.forEach((item) => {
					const data = item.data || {};
					const op = item.op || item.operation || "create";

					html += `<div class="alfred-preview-item">
						<div class="alfred-preview-item-header">
							<span class="badge badge-${op === "create" ? "success" : "warning"}">${op}</span>
							<strong>${frappe.utils.escape_html(data.name || "Unnamed")}</strong>
						</div>`;

					// DocType: show fields table
					if (type === "DocType" && data.fields) {
						html += this.render_fields_table(data.fields);
					}

					// Scripts: show code
					if ((type === "Server Script" || type === "Client Script") && data.script) {
						html += `<pre class="alfred-code-preview"><code>${frappe.utils.escape_html(data.script)}</code></pre>`;
					}

					// Permissions
					if (data.permissions && data.permissions.length) {
						html += this.render_permissions_table(data.permissions);
					}

					html += "</div>";
				});

				html += "</div>";
			});
		}

		preview.html(html);

		// Show action buttons for pending changesets
		if (changeset.status === "Pending") {
			actions.show();
		} else {
			actions.hide();
		}
	}

	render_fields_table(fields) {
		if (!fields.length) return "";
		let html = `<table class="table table-sm alfred-fields-table">
			<thead><tr><th>${__("Field")}</th><th>${__("Type")}</th><th>${__("Label")}</th><th>${__("Required")}</th></tr></thead><tbody>`;
		fields.forEach((f) => {
			if (f.fieldtype === "Section Break" || f.fieldtype === "Column Break" || f.fieldtype === "Tab Break")
				return;
			html += `<tr>
				<td><code>${frappe.utils.escape_html(f.fieldname || "")}</code></td>
				<td>${frappe.utils.escape_html(f.fieldtype || "")}</td>
				<td>${frappe.utils.escape_html(f.label || "")}</td>
				<td>${f.reqd ? "✓" : ""}</td>
			</tr>`;
		});
		html += "</tbody></table>";
		return html;
	}

	render_permissions_table(permissions) {
		let html = `<table class="table table-sm alfred-perms-table">
			<thead><tr><th>${__("Role")}</th><th>R</th><th>W</th><th>C</th><th>D</th></tr></thead><tbody>`;
		permissions.forEach((p) => {
			html += `<tr>
				<td>${frappe.utils.escape_html(p.role || "")}</td>
				<td>${p.read ? "✓" : ""}</td>
				<td>${p.write ? "✓" : ""}</td>
				<td>${p.create ? "✓" : ""}</td>
				<td>${p.delete ? "✓" : ""}</td>
			</tr>`;
		});
		html += "</tbody></table>";
		return html;
	}

	// ── Preview Actions ─────────────────────────────────────────

	approve() {
		if (!this.changeset_data) return;
		const btn = this.container.find(".alfred-approve-btn");
		btn.prop("disabled", true).text(__("Deploying..."));

		frappe.call({
			method: "alfred_client.alfred_settings.page.alfred.alfred.approve_changeset",
			args: { changeset_name: this.changeset_data.name },
			callback: (r) => {
				if (r.message) {
					frappe.show_alert({ message: __("Deployment complete!"), indicator: "green" });
					this.container.find(".alfred-preview-actions").hide();
				}
			},
			error: () => {
				btn.prop("disabled", false).text(__("Approve & Deploy"));
				frappe.show_alert({ message: __("Deployment failed."), indicator: "red" });
			},
		});
	}

	modify() {
		const input = this.container.find(".alfred-input");
		input.prop("disabled", false).focus();
		input.attr("placeholder", __("What would you like to change?"));
		this.container.find(".alfred-send-btn").prop("disabled", false);
	}

	reject() {
		if (!this.changeset_data) return;
		frappe.confirm(__("Are you sure you want to reject this changeset?"), () => {
			frappe.call({
				method: "alfred_client.alfred_settings.page.alfred.alfred.reject_changeset",
				args: { changeset_name: this.changeset_data.name },
				callback: () => {
					frappe.show_alert({ message: __("Changeset rejected."), indicator: "orange" });
					this.container.find(".alfred-preview-actions").hide();
				},
			});
		});
	}

	// ── Real-time Status (Task 5.4) ─────────────────────────────

	setup_realtime() {
		const me = this;

		// Prevent duplicate listeners on repeated page navigation
		if (this._realtime_bound) return;
		this._realtime_bound = true;

		frappe.realtime.on("intern_agent_status", (data) => {
			if (me.current_conversation) {
				me.update_agent_status(data);
				me.append_message({
					role: "system",
					message_type: "status",
					content: `${data.agent || "Agent"}: ${data.status || "working"}`,
					agent_name: data.agent,
				});
				me.scroll_to_bottom();
			}
		});

		frappe.realtime.on("intern_question", (data) => {
			if (me.current_conversation) {
				me.append_message({
					role: "agent",
					message_type: "question",
					content: data.text || data.question || "",
					agent_name: data.agent,
					metadata: JSON.stringify(data),
				});
				me.container.find(".alfred-input").prop("disabled", false);
				me.container.find(".alfred-send-btn").prop("disabled", false);
				me.update_status("Waiting for your response", "waiting");
				me.scroll_to_bottom();
			}
		});

		frappe.realtime.on("intern_preview", (data) => {
			if (me.current_conversation && data.changeset_name) {
				me.load_changeset_preview(data);
			}
		});

		frappe.realtime.on("intern_error", (data) => {
			if (me.current_conversation) {
				me.append_message({
					role: "system",
					message_type: "error",
					content: data.error || data.message || "An error occurred",
				});
				me.container.find(".alfred-input").prop("disabled", false);
				me.container.find(".alfred-send-btn").prop("disabled", false);
				me.update_status("Error", "error");
				me.scroll_to_bottom();
			}
		});

		frappe.realtime.on("intern_deploy_progress", (data) => {
			if (me.current_conversation) {
				me.append_message({
					role: "system",
					message_type: "status",
					content: `Deploy step ${data.step}/${data.total}: ${data.status} — ${data.name || data.doctype}`,
				});
				me.scroll_to_bottom();
			}
		});

		frappe.realtime.on("intern_deploy_complete", (data) => {
			if (me.current_conversation) {
				me.append_message({
					role: "system",
					message_type: "status",
					content: `Deployment complete! ${data.steps} steps executed successfully.`,
				});
				me.update_status("Deployment complete", "success");
				me.container.find(".alfred-input").prop("disabled", false);
				me.container.find(".alfred-send-btn").prop("disabled", false);
				me.scroll_to_bottom();
			}
		});

		frappe.realtime.on("intern_deploy_failed", (data) => {
			if (me.current_conversation) {
				me.append_message({
					role: "system",
					message_type: "error",
					content: `Deployment failed at step ${data.step}: ${data.error}. Rollback initiated.`,
				});
				me.update_status("Deployment failed — rolled back", "error");
				me.container.find(".alfred-input").prop("disabled", false);
				me.container.find(".alfred-send-btn").prop("disabled", false);
				me.scroll_to_bottom();
			}
		});

		// Option button clicks (delegated)
		this.container.on("click", ".alfred-option-btn", function () {
			const value = $(this).data("value");
			me.container.find(".alfred-input").val(value);
			me.send_message();
		});
	}

	update_agent_status(data) {
		const agent = data.agent || "";
		const status = data.status || "";

		// Map agent names to pipeline phases
		const agent_phase_map = {
			requirement: "requirement",
			"Requirement Analyst": "requirement",
			assessment: "assessment",
			"Feasibility Assessor": "assessment",
			architect: "architecture",
			"Solution Architect": "architecture",
			developer: "development",
			"Frappe Developer": "development",
			tester: "testing",
			"QA Validator": "testing",
			deployer: "deployment",
			"Deployment Specialist": "deployment",
		};

		const phase = agent_phase_map[agent] || null;

		if (status === "started" || status === "running") {
			this.update_status(`${agent} is working...`, "processing");
			if (phase) this.highlight_phase(phase);
		} else if (status === "completed") {
			this.update_status(`${agent} completed`, "success");
			if (phase) this.complete_phase(phase);
		}
	}

	update_status(text, state) {
		const dot = this.container.find(".alfred-status-dot");
		const label = this.container.find(".alfred-status-text");

		dot.removeClass("alfred-dot-disconnected alfred-dot-processing alfred-dot-success alfred-dot-error alfred-dot-waiting");
		dot.addClass(`alfred-dot-${state}`);
		label.text(text);
	}

	highlight_phase(phase) {
		this.container.find(".alfred-phase").removeClass("alfred-phase-active");
		this.container.find(`.alfred-phase[data-phase="${phase}"]`).addClass("alfred-phase-active");
	}

	complete_phase(phase) {
		this.container.find(`.alfred-phase[data-phase="${phase}"]`).addClass("alfred-phase-done").removeClass("alfred-phase-active");
	}
}

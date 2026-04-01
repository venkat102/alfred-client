/**
 * Alfred Chat Page
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
		this.changeset_data = null;
		this._phase_start_time = null;
		this._timer_interval = null;
	}

	init() {
		this.render_layout();
		this.setup_realtime();
		this.load_conversations();
		this.page.set_secondary_action(__("New Conversation"), () => this.new_conversation());
	}

	refresh() {
		if (!this.current_conversation) {
			this.load_conversations();
		}
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
						<span class="alfred-elapsed-time text-muted text-xs"></span>
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
								<div class="alfred-input-wrapper">
									<textarea class="alfred-input" placeholder="${__("Describe what you want to build...")}" rows="2"></textarea>
									<span class="alfred-input-hint text-muted text-xs">${__("Enter to send, Shift+Enter for new line")}</span>
								</div>
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
			{ key: "requirement", label: "Requirements" },
			{ key: "assessment", label: "Assessment" },
			{ key: "architecture", label: "Architecture" },
			{ key: "development", label: "Development" },
			{ key: "testing", label: "Testing" },
			{ key: "deployment", label: "Deployment" },
		];
		return phases
			.map(
				(p, i) => `<span class="alfred-phase" data-phase="${p.key}" data-step="${i + 1}">
				<span class="alfred-phase-step">${i + 1}</span>
				<span class="alfred-phase-label">${p.label}</span>
			</span>`
			)
			.join('<span class="alfred-phase-arrow">&rsaquo;</span>');
	}

	// ── Event Handlers ──────────────────────────────────────────

	setup_event_handlers() {
		const me = this;
		this.container.find(".alfred-send-btn").on("click", () => me.send_message());
		this.container.find(".alfred-input").on("keydown", function (e) {
			if (e.key === "Enter" && !e.shiftKey) {
				e.preventDefault();
				me.send_message();
			}
		});
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
				this.container.find(".alfred-conversation-list").html(
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
			// UX-01: Onboarding with example prompts
			list.html(`
				<div class="alfred-onboarding">
					<div class="text-center" style="padding: 30px 20px;">
						<h4 style="margin-bottom: 8px;">${__("Welcome to Alfred")}</h4>
						<p class="text-muted" style="margin-bottom: 24px;">
							${__("I build Frappe customizations through conversation. Tell me what you need.")}
						</p>
						<div class="alfred-example-prompts">
							<p class="text-muted text-xs" style="margin-bottom: 8px; text-transform: uppercase; letter-spacing: 0.5px;">${__("Try one of these")}</p>
							<div class="alfred-example" data-prompt="Create a DocType called Book with title, author, and ISBN fields">
								${__("Create a DocType called Book with title, author, and ISBN fields")}
							</div>
							<div class="alfred-example" data-prompt="Add an approval workflow to Leave Application with Draft, Pending, and Approved states">
								${__("Add an approval workflow to Leave Application")}
							</div>
							<div class="alfred-example" data-prompt="Create a notification that emails the manager when a new expense claim is submitted">
								${__("Notify manager when an expense claim is submitted")}
							</div>
						</div>
						<button class="btn btn-primary btn-md alfred-start-btn" style="margin-top: 20px;">${__("Start a Conversation")}</button>
					</div>
				</div>
			`);

			// Example prompt click handler
			list.find(".alfred-example").on("click", (e) => {
				const prompt = $(e.currentTarget).data("prompt");
				this.new_conversation_with_prompt(prompt);
			});
			list.find(".alfred-start-btn").on("click", () => this.new_conversation());
			return;
		}

		const status_colors = {
			Open: "blue", "In Progress": "orange", "Awaiting Input": "yellow",
			Completed: "green", Escalated: "red", Failed: "red", Stale: "gray",
		};

		let html = '<div class="alfred-conv-items">';
		conversations.forEach((conv) => {
			const color = status_colors[conv.status] || "gray";
			const time = frappe.datetime.prettyDate(conv.modified);
			// UX-03: Show summary instead of hash
			const summary = conv.first_message || conv.name;
			const agent = conv.current_agent ? ` — ${frappe.utils.escape_html(conv.current_agent)}` : "";
			html += `
				<div class="alfred-conv-item" data-name="${conv.name}" tabindex="0" role="button">
					<div class="alfred-conv-header">
						<span class="indicator-pill ${color}">${conv.status}</span>
						<span class="text-muted text-xs">${time}</span>
					</div>
					<div class="alfred-conv-summary">${frappe.utils.escape_html(summary)}${agent}</div>
				</div>
			`;
		});
		html += "</div>";
		list.html(html);

		list.find(".alfred-conv-item").on("click", (e) => {
			this.open_conversation($(e.currentTarget).data("name"));
		});
		// UX-27: Keyboard navigation
		list.find(".alfred-conv-item").on("keydown", (e) => {
			if (e.key === "Enter") {
				this.open_conversation($(e.currentTarget).data("name"));
			}
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

	new_conversation_with_prompt(prompt) {
		frappe.call({
			method: "alfred_client.alfred_settings.page.alfred.alfred.create_conversation",
			callback: (r) => {
				if (r.message) {
					this.open_conversation(r.message.name);
					// Auto-fill and send the example prompt
					this.container.find(".alfred-input").val(prompt);
					setTimeout(() => this.send_message(), 300);
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
		this.container.find(".alfred-input").attr("placeholder", __("Describe what you want to build..."));

		// Reset preview
		this.container.find(".alfred-preview-empty").show();
		this.container.find(".alfred-preview-content").hide().empty();
		this.container.find(".alfred-preview-actions").hide();

		// Reset pipeline
		this.container.find(".alfred-phase").removeClass("alfred-phase-active alfred-phase-done");
		this.update_status(__("Ready"), "disconnected");

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

		this.page.set_title(__("Alfred"));
		this.page.set_primary_action(__("Back"), () => {
			this.container.find(".alfred-conversation-list").show();
			this.container.find(".alfred-chat-area").hide();
			this.page.set_title(__("Alfred"));
			this.page.clear_primary_action();
			this.current_conversation = null;
			this._stop_timer();
			this.load_conversations();
		});
	}

	// ── Messages ────────────────────────────────────────────────

	append_message(msg) {
		// UX-07: Don't add low-value status messages to chat — use the status bar instead
		if (msg.message_type === "status" && msg.role === "system") {
			const content = msg.content || "";
			// Only show milestone messages, not every agent start/complete
			if (content.match(/^(Requirement|Feasibility|Solution|Frappe|QA|Deployment)\s+(Analyst|Assessor|Architect|Developer|Validator|Specialist):\s+(started|completed|running|working)/i)) {
				return; // Skip — the pipeline indicator already shows this
			}
		}

		const area = this.container.find(".alfred-messages");

		// Remove typing indicator if present
		area.find(".alfred-typing-indicator").remove();

		const role_class = `alfred-msg-${msg.role || "system"}`;
		const type_class = `alfred-msg-type-${msg.message_type || "text"}`;
		const agent_badge = msg.agent_name
			? `<span class="alfred-agent-badge">${frappe.utils.escape_html(msg.agent_name)}</span>`
			: "";

		// UX-09: Use full date for old messages
		let time = "";
		if (msg.creation) {
			const msgDate = new Date(msg.creation);
			const now = new Date();
			const diffHours = (now - msgDate) / (1000 * 60 * 60);
			time = diffHours < 24
				? frappe.datetime.prettyDate(msg.creation)
				: frappe.datetime.str_to_user(msg.creation);
		}

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
				content = this.render_error(safe_content);
				break;
			case "changeset":
				content = `<div class="alfred-changeset-msg">${safe_content}</div>`;
				this.load_changeset_preview(msg.metadata);
				break;
			case "preview":
				content = `<div class="alfred-preview-msg">${safe_content}</div>`;
				break;
			default:
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

	// UX-14: Distinct question rendering
	render_question(msg) {
		let html = `<div class="alfred-question-card">
			<div class="alfred-question-icon">?</div>
			<div class="alfred-question-body">
				<div class="alfred-question-text">${frappe.utils.escape_html(msg.content || "")}</div>`;

		try {
			const meta = typeof msg.metadata === "string" ? JSON.parse(msg.metadata) : msg.metadata;
			if (meta && meta.options && Array.isArray(meta.options)) {
				html += '<div class="alfred-question-options">';
				meta.options.forEach((opt) => {
					html += `<button class="btn btn-xs btn-default alfred-option-btn" data-value="${frappe.utils.escape_html(opt)}">${frappe.utils.escape_html(opt)}</button>`;
				});
				html += "</div>";
			}
		} catch (e) {}

		html += `<div class="alfred-question-waiting text-muted text-xs">${__("Alfred is waiting for your response")}</div>
			</div></div>`;
		return html;
	}

	// UX-23: User-friendly error rendering
	render_error(safe_content) {
		// Map common technical errors to human-readable messages
		let user_message = safe_content;
		let technical_detail = "";

		const error_map = [
			[/ValidationError/i, "There was a problem with the data format. Alfred will try a different approach."],
			[/PermissionError/i, "You don't have permission for this operation. Contact your administrator."],
			[/DuplicateEntryError/i, "A document with this name already exists on your site."],
			[/TimedOut|timeout/i, "The operation took too long. Please try again."],
			[/ConnectionError|ECONNREFUSED/i, "Could not connect to the processing service. Please try again later."],
			[/PROMPT_BLOCKED/i, "Your message was flagged by the security filter. Please rephrase your request."],
		];

		for (const [pattern, friendly] of error_map) {
			if (pattern.test(safe_content)) {
				technical_detail = safe_content;
				user_message = friendly;
				break;
			}
		}

		let html = `<div class="alfred-error-msg">
			<div class="alfred-error-user-msg">${user_message}</div>`;

		if (technical_detail && technical_detail !== user_message) {
			html += `<details class="alfred-error-details">
				<summary class="text-xs text-muted">${__("Technical details")}</summary>
				<pre class="text-xs">${technical_detail}</pre>
			</details>`;
		}

		// UX-24: Retry button
		html += `<button class="btn btn-xs btn-default alfred-retry-btn" style="margin-top: 6px;">${__("Retry")}</button>`;
		html += `</div>`;
		return html;
	}

	// UX-06: Typing indicator
	show_typing_indicator() {
		const area = this.container.find(".alfred-messages");
		if (area.find(".alfred-typing-indicator").length) return;
		area.append(`
			<div class="alfred-message alfred-msg-agent alfred-typing-indicator">
				<div class="alfred-typing-dots">
					<span></span><span></span><span></span>
				</div>
			</div>
		`);
		this.scroll_to_bottom();
	}

	remove_typing_indicator() {
		this.container.find(".alfred-typing-indicator").remove();
	}

	render_markdown(escaped_text) {
		let text = escaped_text;
		text = text.replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) => {
			return `<pre class="alfred-code-preview"><code>${code.trim()}</code></pre>`;
		});
		text = text.replace(/`([^`]+)`/g, "<code>$1</code>");
		text = text.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
		text = text.replace(/__(.+?)__/g, "<strong>$1</strong>");
		text = text.replace(/\*(.+?)\*/g, "<em>$1</em>");
		text = text.replace(/^[\-\*]\s+(.+)$/gm, "<li>$1</li>");
		text = text.replace(/(<li>.*<\/li>\n?)+/g, "<ul>$&</ul>");
		text = text.replace(/^\d+\.\s+(.+)$/gm, "<li>$1</li>");
		text = text.replace(/^###\s+(.+)$/gm, "<h6>$1</h6>");
		text = text.replace(/^##\s+(.+)$/gm, "<h5>$1</h5>");
		text = text.replace(/^#\s+(.+)$/gm, "<h4>$1</h4>");
		text = text.replace(/\n/g, "<br>");
		text = text.replace(/<\/(pre|ul|li|h[456])><br>/g, "</$1>");
		return text;
	}

	send_message() {
		const input = this.container.find(".alfred-input");
		const message = input.val().trim();
		if (!message || !this.current_conversation) return;

		input.val("");

		this.append_message({
			role: "user",
			message_type: "text",
			content: message,
			creation: frappe.datetime.now_datetime(),
		});
		this.scroll_to_bottom();

		// UX-06: Show typing indicator
		this.show_typing_indicator();

		input.prop("disabled", true);
		this.container.find(".alfred-send-btn").prop("disabled", true);
		this.update_status(__("Processing..."), "processing");
		this._start_timer();

		frappe.call({
			method: "alfred_client.alfred_settings.page.alfred.alfred.send_message",
			args: { conversation: this.current_conversation, message: message },
			callback: () => {},
			error: () => {
				this.remove_typing_indicator();
				input.prop("disabled", false);
				this.container.find(".alfred-send-btn").prop("disabled", false);
				this.update_status(__("Error sending message"), "error");
				this._stop_timer();
			},
		});
	}

	scroll_to_bottom() {
		const area = this.container.find(".alfred-messages");
		if (area[0]) area.scrollTop(area[0].scrollHeight);
	}

	// ── Timer (UX-12) ───────────────────────────────────────────

	_start_timer() {
		this._phase_start_time = Date.now();
		this._stop_timer();
		this._timer_interval = setInterval(() => {
			if (this._phase_start_time) {
				const elapsed = Math.round((Date.now() - this._phase_start_time) / 1000);
				this.container.find(".alfred-elapsed-time").text(`(${elapsed}s)`);
			}
		}, 1000);
	}

	_stop_timer() {
		if (this._timer_interval) {
			clearInterval(this._timer_interval);
			this._timer_interval = null;
		}
		this.container.find(".alfred-elapsed-time").text("");
		this._phase_start_time = null;
	}

	// ── Preview Panel ───────────────────────────────────────────

	// UX-16: Show progressive content in preview during early phases
	show_preview_progress(phase, data) {
		const preview = this.container.find(".alfred-preview-content");
		const empty = this.container.find(".alfred-preview-empty");
		empty.hide();
		preview.show();

		let html = "";
		if (phase === "requirement") {
			html = `<h5 class="alfred-preview-title">${__("Gathering Requirements...")}</h5>
				<div class="alfred-preview-progress-content text-muted">
					<p>${__("Alfred is understanding your request and identifying what needs to be built.")}</p>
				</div>`;
		} else if (phase === "assessment") {
			html = `<h5 class="alfred-preview-title">${__("Checking Feasibility...")}</h5>
				<div class="alfred-preview-progress-content text-muted">
					<p>${__("Verifying permissions and checking for conflicts with existing customizations.")}</p>
				</div>`;
		} else if (phase === "architecture") {
			html = `<h5 class="alfred-preview-title">${__("Designing Solution...")}</h5>
				<div class="alfred-preview-progress-content text-muted">
					<p>${__("Planning DocTypes, fields, relationships, and scripts.")}</p>
				</div>`;
		} else if (phase === "development") {
			html = `<h5 class="alfred-preview-title">${__("Generating Code...")}</h5>
				<div class="alfred-preview-progress-content text-muted">
					<p>${__("Writing DocType definitions, Server Scripts, and Client Scripts.")}</p>
				</div>`;
		} else if (phase === "testing") {
			html = `<h5 class="alfred-preview-title">${__("Validating...")}</h5>
				<div class="alfred-preview-progress-content text-muted">
					<p>${__("Checking syntax, permissions, naming conflicts, and deployment order.")}</p>
				</div>`;
		}
		if (html) preview.html(html);
	}

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
		} catch (e) {}
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
			const groups = {};
			changeset.changes.forEach((change) => {
				const dt = change.doctype || change.data?.doctype || "Other";
				if (!groups[dt]) groups[dt] = [];
				groups[dt].push(change);
			});

			// UX-19: Summary before action buttons
			const totalOps = changeset.changes.length;
			html += `<div class="alfred-preview-summary">
				<span class="text-muted">${__("{0} operation(s) will be applied to your site", [totalOps])}</span>
			</div>`;

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

					if (type === "DocType" && data.fields) {
						html += this.render_fields_table(data.fields);
					}
					if ((type === "Server Script" || type === "Client Script") && data.script) {
						html += `<pre class="alfred-code-preview"><code>${frappe.utils.escape_html(data.script)}</code></pre>`;
					}
					if (data.permissions && data.permissions.length) {
						html += this.render_permissions_table(data.permissions);
					}
					html += "</div>";
				});
				html += "</div>";
			});
		}

		preview.html(html);

		if (changeset.status === "Pending") {
			actions.show();
		} else {
			actions.hide();
		}
	}

	render_fields_table(fields) {
		if (!fields.length) return "";
		let html = `<table class="table table-sm alfred-fields-table">
			<thead><tr>
				<th>${__("Field")}</th><th>${__("Type")}</th>
				<th>${__("Label")}</th><th>${__("Required")}</th>
			</tr></thead><tbody>`;
		fields.forEach((f) => {
			if (["Section Break", "Column Break", "Tab Break"].includes(f.fieldtype)) return;
			html += `<tr>
				<td><code>${frappe.utils.escape_html(f.fieldname || "")}</code></td>
				<td>${frappe.utils.escape_html(f.fieldtype || "")}</td>
				<td>${frappe.utils.escape_html(f.label || "")}</td>
				<td>${f.reqd ? "Yes" : ""}</td>
			</tr>`;
		});
		html += "</tbody></table>";
		return html;
	}

	render_permissions_table(permissions) {
		let html = `<table class="table table-sm alfred-perms-table">
			<thead><tr>
				<th>${__("Role")}</th>
				<th title="Read">Read</th><th title="Write">Write</th>
				<th title="Create">Create</th><th title="Delete">Delete</th>
			</tr></thead><tbody>`;
		permissions.forEach((p) => {
			html += `<tr>
				<td>${frappe.utils.escape_html(p.role || "")}</td>
				<td>${p.read ? "✓" : ""}</td><td>${p.write ? "✓" : ""}</td>
				<td>${p.create ? "✓" : ""}</td><td>${p.delete ? "✓" : ""}</td>
			</tr>`;
		});
		html += "</tbody></table>";
		return html;
	}

	// ── Preview Actions ─────────────────────────────────────────

	// UX-19: Deployment confirmation dialog
	approve() {
		if (!this.changeset_data) return;

		const changes = this.changeset_data.changes || [];
		const summary = changes.map((c) => {
			const op = c.op || c.operation || "create";
			const name = (c.data || {}).name || "Unnamed";
			const dt = c.doctype || "Document";
			return `<li><strong>${op}</strong> ${dt}: ${frappe.utils.escape_html(name)}</li>`;
		}).join("");

		frappe.confirm(
			`<p><strong>${__("Deploy to your live site?")}</strong></p>
			 <p class="text-muted">${__("The following changes will be applied:")}</p>
			 <ul style="text-align:left; margin: 10px 0;">${summary}</ul>
			 <p class="text-muted text-xs">${__("Changes can be rolled back if needed.")}</p>`,
			() => {
				const btn = this.container.find(".alfred-approve-btn");
				btn.prop("disabled", true).text(__("Deploying..."));

				frappe.call({
					method: "alfred_client.alfred_settings.page.alfred.alfred.approve_changeset",
					args: { changeset_name: this.changeset_data.name },
					callback: (r) => {
						if (r.message) {
							// UX-21: Show success state in preview
							this.container.find(".alfred-preview-actions").hide();
							this.container.find(".alfred-preview-content").prepend(
								`<div class="alfred-deploy-success">
									<span style="color: var(--green-600);">&#10003;</span>
									${__("All changes deployed successfully")}
								</div>`
							);
							frappe.show_alert({ message: __("Deployment complete!"), indicator: "green" });
						}
					},
					error: () => {
						btn.prop("disabled", false).text(__("Approve & Deploy"));
						frappe.show_alert({ message: __("Deployment failed. Check the chat for details."), indicator: "red" });
					},
				});
			}
		);
	}

	modify() {
		const input = this.container.find(".alfred-input");
		// UX-08: Context-aware placeholder
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

	// ── Real-time Events ────────────────────────────────────────

	setup_realtime() {
		const me = this;
		if (this._realtime_bound) return;
		this._realtime_bound = true;

		frappe.realtime.on("intern_agent_status", (data) => {
			if (!me.current_conversation) return;
			me.remove_typing_indicator();
			me.update_agent_status(data);

			// UX-07: Only show milestone messages, not every start/complete
			const status = data.status || "";
			if (status === "completed" && data.agent) {
				me.append_message({
					role: "system", message_type: "status",
					content: `${data.agent} completed`,
				});
			} else if (status === "escalated") {
				me.append_message({
					role: "system", message_type: "error",
					content: data.message || "Escalated to human developer",
				});
			}
			me.scroll_to_bottom();
		});

		frappe.realtime.on("intern_question", (data) => {
			if (!me.current_conversation) return;
			me.remove_typing_indicator();
			me._stop_timer();
			me.append_message({
				role: "agent", message_type: "question",
				content: data.text || data.question || "",
				agent_name: data.agent,
				metadata: JSON.stringify(data),
			});
			me.container.find(".alfred-input").prop("disabled", false);
			me.container.find(".alfred-send-btn").prop("disabled", false);
			// UX-08: Context-aware placeholder
			me.container.find(".alfred-input").attr("placeholder", __("Type your answer..."));
			me.update_status(__("Waiting for your response"), "waiting");
			me.scroll_to_bottom();
		});

		frappe.realtime.on("intern_preview", (data) => {
			if (me.current_conversation && data.changeset_name) {
				me.load_changeset_preview(data);
			}
		});

		frappe.realtime.on("intern_error", (data) => {
			if (!me.current_conversation) return;
			me.remove_typing_indicator();
			me._stop_timer();
			me.append_message({
				role: "system", message_type: "error",
				content: data.error || data.message || "An error occurred",
			});
			me.container.find(".alfred-input").prop("disabled", false);
			me.container.find(".alfred-send-btn").prop("disabled", false);
			me.update_status(__("Error"), "error");
			me.scroll_to_bottom();
		});

		frappe.realtime.on("intern_deploy_progress", (data) => {
			if (!me.current_conversation) return;
			// UX-22: Show deploy progress in preview, not as chat messages
			me._update_deploy_progress(data);
		});

		frappe.realtime.on("intern_deploy_complete", (data) => {
			if (!me.current_conversation) return;
			me._stop_timer();
			me.append_message({
				role: "system", message_type: "status",
				content: `Deployment complete! ${data.steps} steps executed successfully.`,
			});
			me.update_status(__("Deployment complete"), "success");
			me.container.find(".alfred-input").prop("disabled", false);
			me.container.find(".alfred-send-btn").prop("disabled", false);
			me.container.find(".alfred-input").attr("placeholder", __("Ask a follow-up or start a new request..."));
			me.scroll_to_bottom();
		});

		frappe.realtime.on("intern_deploy_failed", (data) => {
			if (!me.current_conversation) return;
			me._stop_timer();
			me.append_message({
				role: "system", message_type: "error",
				content: `Deployment failed at step ${data.step}: ${data.error}. All changes rolled back.`,
			});
			me.update_status(__("Deployment failed — rolled back"), "error");
			me.container.find(".alfred-input").prop("disabled", false);
			me.container.find(".alfred-send-btn").prop("disabled", false);
			me.scroll_to_bottom();
		});

		// Option button and retry button clicks (delegated)
		this.container.on("click", ".alfred-option-btn", function () {
			const value = $(this).data("value");
			me.container.find(".alfred-input").val(value);
			me.send_message();
		});

		this.container.on("click", ".alfred-retry-btn", function () {
			// Find the last user message and resend it
			const userMsgs = me.container.find(".alfred-msg-user .alfred-text-msg");
			if (userMsgs.length) {
				const lastMsg = userMsgs.last().text().trim();
				if (lastMsg) {
					me.container.find(".alfred-input").val(lastMsg);
					me.send_message();
				}
			}
		});
	}

	// UX-22: Visual deploy progress in preview panel
	_update_deploy_progress(data) {
		const preview = this.container.find(".alfred-preview-content");
		let progressBar = preview.find(".alfred-deploy-progress");

		if (!progressBar.length) {
			preview.prepend(`<div class="alfred-deploy-progress">
				<h6>${__("Deploying...")}</h6>
				<div class="alfred-deploy-steps"></div>
			</div>`);
			progressBar = preview.find(".alfred-deploy-progress");
		}

		const steps = progressBar.find(".alfred-deploy-steps");
		const icon = data.status === "success" ? "✓" : data.status === "in_progress" ? "⏳" : "✗";
		const color = data.status === "success" ? "green" : data.status === "in_progress" ? "orange" : "red";

		// Update or add step
		let stepEl = steps.find(`[data-step="${data.step}"]`);
		if (!stepEl.length) {
			steps.append(`<div class="alfred-deploy-step" data-step="${data.step}" style="color: var(--${color}-600);">
				<span>${icon}</span>
				<span>Step ${data.step}/${data.total}: ${frappe.utils.escape_html(data.name || data.doctype || "")}</span>
			</div>`);
		} else {
			stepEl.html(`<span>${icon}</span> <span>Step ${data.step}/${data.total}: ${frappe.utils.escape_html(data.name || data.doctype || "")}</span>`);
			stepEl.css("color", `var(--${color}-600)`);
		}
	}

	update_agent_status(data) {
		const agent = data.agent || "";
		const status = data.status || "";

		const agent_phase_map = {
			requirement: "requirement", "Requirement Analyst": "requirement",
			assessment: "assessment", "Feasibility Assessor": "assessment",
			architect: "architecture", "Solution Architect": "architecture",
			developer: "development", "Frappe Developer": "development",
			tester: "testing", "QA Validator": "testing",
			deployer: "deployment", "Deployment Specialist": "deployment",
			Orchestrator: null,
		};

		const phase = agent_phase_map[agent] || null;

		if (status === "started" || status === "running") {
			// UX-12: Show step number
			const step = phase ? this.container.find(`.alfred-phase[data-phase="${phase}"]`).data("step") : "";
			const stepText = step ? `Step ${step}/6 — ` : "";
			this.update_status(`${stepText}${agent} is working...`, "processing");
			if (phase) {
				this.highlight_phase(phase);
				// UX-16: Progressive preview content
				this.show_preview_progress(phase, data);
			}
			this._start_timer();
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

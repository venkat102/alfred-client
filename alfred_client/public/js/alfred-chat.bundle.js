import { createApp } from "vue";
import AlfredChatApp from "./alfred_chat/AlfredChatApp.vue";

class AlfredChat {
	constructor({ wrapper, page }) {
		this.$wrapper = $(wrapper);
		this.page = page;
		this.init();
	}

	init() {
		// Permission check before rendering
		frappe.call({
			method: "alfred_client.api.permissions.has_app_permission",
			async: false,
			callback: (r) => {
				if (!r.message) {
					this.$wrapper.html(
						`<div class="text-center" style="padding: 80px 20px;">
							<h4>${__("Not Authorized")}</h4>
							<p class="text-muted">${__("You do not have permission to access Alfred. Contact your administrator to request access.")}</p>
						</div>`
					);
					return;
				}
				this.setup_app();
			},
		});
	}

	setup_app() {
		let app = createApp(AlfredChatApp);
		SetVueGlobals(app);
		this.$component = app.mount(this.$wrapper.get(0));
	}
}

frappe.provide("frappe.ui");
frappe.ui.AlfredChat = AlfredChat;

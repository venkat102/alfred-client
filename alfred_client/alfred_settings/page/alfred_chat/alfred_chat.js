/**
 * Alfred Chat Page - Vue 3 entry point
 *
 * Mounts the Vue app inside the Frappe page shell.
 * The actual UI is in public/js/alfred/*.vue components.
 */

frappe.pages["alfred-chat"].on_page_load = function (wrapper) {
	var page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Alfred"),
		single_column: true,
	});

	page.main.addClass("alfred-page");

	// Client-side access control
	frappe.call({
		method: "alfred_client.api.permissions.has_app_permission",
		async: false,
		callback: function (r) {
			if (!r.message) {
				$(page.main).html(
					`<div class="text-center" style="padding: 80px 20px;">
						<h4>${__("Not Authorized")}</h4>
						<p class="text-muted">${__("You do not have permission to access Alfred. Contact your administrator to request access.")}</p>
					</div>`
				);
				return;
			}

			// Mount Vue 3 app
			const { createApp } = Vue;
			const AlfredChat = frappe.require("alfred_client.bundle.js")?.AlfredChat;

			if (AlfredChat) {
				const app = createApp(AlfredChat);
				const vm = app.mount(page.main[0]);
				page.alfred_vm = vm;

				page.set_secondary_action(__("New Conversation"), () => {
					if (vm.currentConversation) {
						vm.goBack();
					}
				});
			} else {
				// Fallback: Vue components not built yet - show instructions
				$(page.main).html(
					`<div class="text-center" style="padding: 60px 20px;">
						<h4>${__("Alfred")}</h4>
						<p class="text-muted">${__("Vue components need to be built. Run:")} <code>bench build --app alfred_client</code></p>
					</div>`
				);
			}
		},
	});
};

frappe.pages["alfred-chat"].on_page_show = function (wrapper) {
	// Vue handles reactivity - no manual refresh needed
};

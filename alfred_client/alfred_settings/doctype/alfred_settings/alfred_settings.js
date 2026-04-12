frappe.ui.form.on("Alfred Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Test LLM Connection"), () => {
			frappe.show_alert({ message: __("Testing LLM connection..."), indicator: "blue" });
			frappe.call({
				method: "alfred_client.alfred_settings.doctype.alfred_settings.alfred_settings.test_llm_connection",
				callback(r) {
					if (!r.message) return;
					let d = r.message;
					let indicator = { ok: "green", warning: "orange", error: "red" }[d.status] || "grey";
					frappe.msgprint({
						title: __("LLM Connection Test"),
						indicator: indicator,
						message: d.message,
					});
				},
			});
		}, __("Actions"));

		frm.add_custom_button(__("Test Processing App"), () => {
			let url = frm.doc.processing_app_url;
			if (!url) {
				frappe.msgprint(__("Processing App URL is not configured."));
				return;
			}
			frappe.show_alert({ message: __("Checking Processing App..."), indicator: "blue" });
			frappe.call({
				method: "alfred_client.alfred_settings.doctype.alfred_settings.alfred_settings.check_processing_app",
				callback(r) {
					if (!r.message) return;
					let d = r.message;
					let ok = d.reachable;
					frappe.msgprint({
						title: __("Processing App"),
						indicator: ok ? "green" : "red",
						message: ok
							? __("Connected. Version: {0}, Redis: {1}", [d.version || "?", d.redis || "?"])
							: __("Cannot reach Processing App at {0}. Error: {1}", [url, d.error || "unknown"]),
					});
				},
			});
		}, __("Actions"));
	},
});

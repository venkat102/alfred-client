frappe.pages["alfred-chat"].on_page_load = function (wrapper) {
	frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Alfred Chat"),
		single_column: true,
	});
};

frappe.pages["alfred-chat"].on_page_show = function (wrapper) {
	let $parent = $(wrapper).find(".layout-main-section");
	$parent.empty();

	frappe.require("alfred-chat.bundle.js").then(() => {
		wrapper.alfred_chat = new frappe.ui.AlfredChat({
			wrapper: $parent,
			page: wrapper.page,
		});
	});
};

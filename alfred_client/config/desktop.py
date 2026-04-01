from frappe import _


def get_data():
	return [
		{
			"module_name": "Alfred",
			"color": "#6C63FF",
			"icon": "octicon octicon-hubot",
			"type": "module",
			"label": _("Alfred"),
			"description": _("AI-powered agent for building Frappe customizations"),
		},
		{
			"module_name": "Alfred Settings",
			"color": "#6C63FF",
			"icon": "octicon octicon-gear",
			"type": "module",
			"label": _("Alfred Settings"),
			"description": _("Alfred configuration and tracking"),
			"hidden": 1,
		},
	]

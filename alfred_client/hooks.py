app_name = "alfred_client"
app_title = "Alfred"
app_publisher = "Venkatesh"
app_description = "AI-powered agent for building Frappe customizations through conversation"
app_email = "venkatesh@example.com"
app_license = "MIT"
source_link = ""
app_logo_url = "/assets/alfred_client/images/alfred-logo.svg"

# Required Apps
required_apps = ["frappe"]

# Module Registration
# Alfred: Module where AI agents create DocTypes on the customer's site
# Alfred Settings: Module for Alfred's own configuration and tracking DocTypes

# Apps Screen Configuration
add_to_apps_screen = [
	{
		"name": "alfred_client",
		"logo": "/assets/alfred_client/images/alfred-logo.svg",
		"title": "Alfred",
		"route": "/app/alfred-chat",
		"has_permission": "alfred_client.api.permissions.has_app_permission",
	}
]

# Global Assets
# app_include_js = "/assets/alfred_client/js/alfred.bundle.js"
# app_include_css = "/assets/alfred_client/css/alfred.bundle.css"

# Website Assets
# website_route_rules = [
# 	{"from_route": "/alfred/<path:app_path>", "to_route": "alfred"},
# ]

# DocType Events
# doc_events = {}

# Scheduled Tasks
scheduler_events = {
	"hourly": [
		"alfred_client.api.stale_cleanup.mark_stale_conversations",
	],
	"daily": [
		"alfred_client.api.stale_cleanup.cleanup_old_audit_logs",
	],
}

# Installation Hooks
after_install = "alfred_client.install.after_install"
before_uninstall = "alfred_client.uninstall.before_uninstall"

# Migration Hooks
# Rebuild the Framework Knowledge Graph after every migrate so agents always
# query the exact DocType schemas of the currently-installed bench apps.
after_migrate = "alfred_client.mcp.framework_kg.build_knowledge_graph"

# Fixtures
# fixtures = []

# Jinja Environment
# jinja = {}

# Override DocType Classes
# override_doctype_class = {}

# Permissions
permission_query_conditions = {
	"Alfred Conversation": "alfred_client.api.permissions.conversation_query_conditions",
	"Alfred Message": "alfred_client.api.permissions.message_query_conditions",
	"Alfred Changeset": "alfred_client.api.permissions.changeset_query_conditions",
}
has_permission = {
	"Alfred Conversation": "alfred_client.api.permissions.conversation_has_permission",
	"Alfred Message": "alfred_client.api.permissions.message_has_permission",
	"Alfred Changeset": "alfred_client.api.permissions.changeset_has_permission",
}

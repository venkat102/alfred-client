<template>
	<div class="alfred-preview">
		<!-- Deployed success banner -->
		<div v-if="deployed" class="alfred-deploy-success">
			&#10003; {{ __("All changes deployed successfully") }}
		</div>

		<!-- Deploy progress -->
		<div v-if="deploySteps.length" class="alfred-deploy-progress">
			<h6>{{ __("Deploying...") }}</h6>
			<div v-for="step in deploySteps" :key="step.step" class="alfred-deploy-step"
				:style="{ color: stepColor(step.status) }">
				<span>{{ stepIcon(step.status) }}</span>
				<span>Step {{ step.step }}/{{ step.total }}: {{ step.name || step.doctype }}</span>
			</div>
		</div>

		<!-- Empty state -->
		<div v-if="!changeset && !currentPhase" class="alfred-preview-empty">
			<div class="text-muted text-center" style="padding: 60px 20px;">
				<i class="fa fa-eye" style="font-size: 48px; margin-bottom: 16px; display: block; opacity: 0.3;"></i>
				<h5>{{ __("Preview Panel") }}</h5>
				<p>{{ __("Changes proposed by Alfred will appear here for your review.") }}</p>
			</div>
		</div>

		<!-- Progressive phase content -->
		<div v-else-if="!changeset && currentPhase" class="alfred-preview-progress-content text-center" style="padding: 40px;">
			<h5 class="alfred-preview-title">{{ phaseTitle }}</h5>
			<p class="text-muted">{{ phaseDescription }}</p>
		</div>

		<!-- Changeset preview -->
		<div v-else-if="changeset" class="alfred-preview-content">
			<!-- Dry-run validation banner (pre-deploy) -->
			<div v-if="changeset.status === 'Pending' && changeset.dry_run_valid === 1" class="alfred-validation-banner alfred-validation-ok">
				&#10003; {{ __("Validated - ready to deploy") }}
			</div>
			<div v-else-if="changeset.status === 'Pending' && dryRunIssues.length" class="alfred-validation-banner alfred-validation-warn">
				<div class="alfred-validation-heading">
					&#9888; {{ __("{0} validation issue(s) found - review before deploying", [dryRunIssues.length]) }}
				</div>
				<ul class="alfred-validation-issues">
					<li v-for="(issue, i) in dryRunIssues" :key="i" :class="`alfred-issue-${issue.severity || 'warning'}`">
						<strong>{{ (issue.severity || 'warning').toUpperCase() }}:</strong>
						<span v-if="issue.doctype"> {{ issue.doctype }}<span v-if="issue.name"> ({{ issue.name }})</span> - </span>
						{{ issue.issue || issue.message }}
					</li>
				</ul>
			</div>

			<!-- Status banner -->
			<div v-if="changeset.status === 'Deployed'" class="alfred-deploy-success" style="margin-bottom: 12px;">
				&#10003; {{ __("Deployed successfully") }}
			</div>
			<div v-else-if="changeset.status === 'Rejected'" class="alfred-status-banner alfred-status-rejected" style="margin-bottom: 12px;">
				&#10007; {{ __("Changeset rejected") }}
			</div>
			<div v-else-if="changeset.status === 'Rolled Back'" class="alfred-status-banner alfred-status-rolled-back" style="margin-bottom: 12px;">
				&#8634; {{ __("Deployment rolled back") }}
			</div>

			<h5 class="alfred-preview-title">{{ __("Changeset Preview") }}</h5>
			<div class="alfred-preview-summary">
				{{ __("{0} operation(s)", [changes.length]) }}
			</div>

			<div v-for="(items, type) in groupedChanges" :key="type" class="alfred-preview-group">
				<h6 class="alfred-preview-group-title">{{ type }}s ({{ items.length }})</h6>
				<div v-for="(item, idx) in items" :key="idx" class="alfred-preview-item">
					<div class="alfred-preview-item-header">
						<span :class="['badge', item.op === 'create' ? 'badge-success' : 'badge-warning']">
							{{ item.op || item.operation || "create" }}
						</span>
						<strong>{{ (item.data || {}).name || "Unnamed" }}</strong>
					</div>

					<!-- Fields table for DocTypes -->
					<table v-if="type === 'DocType' && item.data" class="table table-sm alfred-detail-table">
						<tbody>
							<tr v-if="item.data.module"><td class="text-muted">Module</td><td>{{ item.data.module }}</td></tr>
							<tr v-if="item.data.naming_rule"><td class="text-muted">Naming Rule</td><td>{{ item.data.naming_rule }}</td></tr>
							<tr v-if="item.data.autoname"><td class="text-muted">Autoname</td><td><code>{{ item.data.autoname }}</code></td></tr>
							<tr v-if="item.data.is_submittable"><td class="text-muted">Submittable</td><td>Yes</td></tr>
							<tr v-if="item.data.is_tree"><td class="text-muted">Tree</td><td>Yes</td></tr>
							<tr v-if="item.data.is_single"><td class="text-muted">Single</td><td>Yes</td></tr>
							<tr v-if="item.data.description"><td class="text-muted">Description</td><td>{{ item.data.description }}</td></tr>
						</tbody>
					</table>
					<table v-if="type === 'DocType' && item.data?.fields?.length" class="table table-sm alfred-fields-table">
						<thead><tr><th>Field</th><th>Type</th><th>Label</th><th>Options</th><th>Required</th></tr></thead>
						<tbody>
							<tr v-for="f in visibleFields(item.data.fields)" :key="f.fieldname">
								<td><code>{{ f.fieldname }}</code></td>
								<td>{{ f.fieldtype }}</td>
								<td>{{ f.label }}</td>
								<td><span v-if="f.options" class="text-muted">{{ f.options }}</span></td>
								<td>{{ f.reqd ? 'Yes' : '' }}</td>
							</tr>
						</tbody>
					</table>

					<!-- Notification details -->
					<table v-if="type === 'Notification' && item.data" class="table table-sm alfred-detail-table">
						<tbody>
							<tr v-if="item.data.document_type"><td class="text-muted">Document Type</td><td>{{ item.data.document_type }}</td></tr>
							<tr v-if="item.data.event"><td class="text-muted">Event</td><td>{{ item.data.event }}</td></tr>
							<tr v-if="item.data.channel"><td class="text-muted">Channel</td><td>{{ item.data.channel }}</td></tr>
							<tr v-if="item.data.subject"><td class="text-muted">Subject</td><td>{{ item.data.subject }}</td></tr>
							<tr v-if="item.data.condition"><td class="text-muted">Condition</td><td><code>{{ item.data.condition }}</code></td></tr>
							<tr v-if="recipientsSummary(item.data)"><td class="text-muted">Recipients</td><td>{{ recipientsSummary(item.data) }}</td></tr>
							<tr v-if="item.data.days_in_advance"><td class="text-muted">Days in Advance</td><td>{{ item.data.days_in_advance }}</td></tr>
							<tr v-if="item.data.date_changed"><td class="text-muted">Date Field</td><td><code>{{ item.data.date_changed }}</code></td></tr>
							<tr v-if="item.data.value_changed"><td class="text-muted">Value Changed Field</td><td><code>{{ item.data.value_changed }}</code></td></tr>
							<tr><td class="text-muted">Enabled</td><td>{{ item.data.enabled === 0 ? 'No' : 'Yes' }}</td></tr>
						</tbody>
					</table>
					<div v-if="type === 'Notification' && item.data?.message" class="alfred-msg-preview">
						<small class="text-muted">Message template:</small>
						<div class="alfred-code-preview" v-html="item.data.message"></div>
					</div>

					<!-- Server Script details -->
					<table v-if="type === 'Server Script' && item.data" class="table table-sm alfred-detail-table">
						<tbody>
							<tr v-if="item.data.reference_doctype"><td class="text-muted">DocType</td><td>{{ item.data.reference_doctype }}</td></tr>
							<tr v-if="item.data.script_type"><td class="text-muted">Script Type</td><td>{{ item.data.script_type }}</td></tr>
							<tr v-if="item.data.doctype_event"><td class="text-muted">DocType Event</td><td>{{ item.data.doctype_event }}</td></tr>
							<tr v-if="item.data.api_method"><td class="text-muted">API Method</td><td><code>{{ item.data.api_method }}</code></td></tr>
							<tr v-if="item.data.event_frequency"><td class="text-muted">Frequency</td><td>{{ item.data.event_frequency }}</td></tr>
							<tr v-if="item.data.cron_format"><td class="text-muted">Cron</td><td><code>{{ item.data.cron_format }}</code></td></tr>
							<tr><td class="text-muted">Disabled</td><td>{{ item.data.disabled ? 'Yes' : 'No' }}</td></tr>
						</tbody>
					</table>
					<pre v-if="type === 'Server Script' && item.data?.script" class="alfred-code-preview"><code>{{ item.data.script }}</code></pre>

					<!-- Client Script details -->
					<table v-if="type === 'Client Script' && item.data" class="table table-sm alfred-detail-table">
						<tbody>
							<tr v-if="item.data.dt"><td class="text-muted">DocType</td><td>{{ item.data.dt }}</td></tr>
							<tr v-if="item.data.view"><td class="text-muted">View</td><td>{{ item.data.view }}</td></tr>
							<tr><td class="text-muted">Enabled</td><td>{{ item.data.enabled === 0 ? 'No' : 'Yes' }}</td></tr>
						</tbody>
					</table>
					<pre v-if="type === 'Client Script' && item.data?.script" class="alfred-code-preview"><code>{{ item.data.script }}</code></pre>

					<!-- Custom Field details -->
					<table v-if="type === 'Custom Field' && item.data" class="table table-sm alfred-detail-table">
						<tbody>
							<tr v-if="item.data.dt"><td class="text-muted">Target DocType</td><td>{{ item.data.dt }}</td></tr>
							<tr v-if="item.data.fieldname"><td class="text-muted">Field Name</td><td><code>{{ item.data.fieldname }}</code></td></tr>
							<tr v-if="item.data.fieldtype"><td class="text-muted">Field Type</td><td>{{ item.data.fieldtype }}</td></tr>
							<tr v-if="item.data.label"><td class="text-muted">Label</td><td>{{ item.data.label }}</td></tr>
							<tr v-if="item.data.options"><td class="text-muted">Options</td><td>{{ item.data.options }}</td></tr>
							<tr v-if="item.data.default"><td class="text-muted">Default</td><td><code>{{ item.data.default }}</code></td></tr>
							<tr v-if="item.data.insert_after"><td class="text-muted">Insert After</td><td>{{ item.data.insert_after }}</td></tr>
							<tr><td class="text-muted">Required</td><td>{{ item.data.reqd ? 'Yes' : 'No' }}</td></tr>
							<tr v-if="item.data.in_list_view"><td class="text-muted">In List View</td><td>Yes</td></tr>
							<tr v-if="item.data.in_standard_filter"><td class="text-muted">In Filter</td><td>Yes</td></tr>
							<tr v-if="item.data.description"><td class="text-muted">Description</td><td>{{ item.data.description }}</td></tr>
						</tbody>
					</table>

					<!-- Workflow details -->
					<table v-if="type === 'Workflow' && item.data" class="table table-sm alfred-detail-table">
						<tbody>
							<tr v-if="item.data.workflow_name"><td class="text-muted">Workflow Name</td><td>{{ item.data.workflow_name }}</td></tr>
							<tr v-if="item.data.document_type"><td class="text-muted">DocType</td><td>{{ item.data.document_type }}</td></tr>
							<tr v-if="item.data.workflow_state_field"><td class="text-muted">State Field</td><td><code>{{ item.data.workflow_state_field }}</code></td></tr>
							<tr><td class="text-muted">Active</td><td>{{ item.data.is_active ? 'Yes' : 'No' }}</td></tr>
							<tr v-if="item.data.send_email_alert !== undefined"><td class="text-muted">Email Alert</td><td>{{ item.data.send_email_alert ? 'Yes' : 'No' }}</td></tr>
							<tr v-if="item.data.override_status !== undefined"><td class="text-muted">Override Status</td><td>{{ item.data.override_status ? 'Yes' : 'No' }}</td></tr>
						</tbody>
					</table>
					<div v-if="type === 'Workflow' && item.data?.states?.length">
						<small class="text-muted">States ({{ item.data.states.length }})</small>
						<table class="table table-sm alfred-fields-table">
							<thead><tr><th>State</th><th>Doc Status</th><th>Allow Edit</th><th>Update Field</th></tr></thead>
							<tbody>
								<tr v-for="(s, si) in item.data.states" :key="'state-' + si">
									<td><strong>{{ s.state }}</strong></td>
									<td>{{ docStatusLabel(s.doc_status) }}</td>
									<td>{{ s.allow_edit || '-' }}</td>
									<td><span v-if="s.update_field"><code>{{ s.update_field }}</code> = {{ s.update_value }}</span></td>
								</tr>
							</tbody>
						</table>
					</div>
					<div v-if="type === 'Workflow' && item.data?.transitions?.length">
						<small class="text-muted">Transitions ({{ item.data.transitions.length }})</small>
						<table class="table table-sm alfred-fields-table">
							<thead><tr><th>From State</th><th>Action</th><th>To State</th><th>Allowed</th><th>Condition</th></tr></thead>
							<tbody>
								<tr v-for="(t, ti) in item.data.transitions" :key="'trans-' + ti">
									<td>{{ t.state }}</td>
									<td><strong>{{ t.action }}</strong></td>
									<td>{{ t.next_state }}</td>
									<td>{{ t.allowed || '-' }}</td>
									<td><code v-if="t.condition">{{ t.condition }}</code></td>
								</tr>
							</tbody>
						</table>
					</div>

					<!-- Generic key-value for any other type not handled above -->
					<table v-if="!['DocType','Notification','Server Script','Client Script','Custom Field','Workflow'].includes(type) && item.data"
						class="table table-sm alfred-detail-table">
						<tbody>
							<tr v-for="(val, key) in displayableFields(item.data)" :key="key">
								<td class="text-muted">{{ key }}</td>
								<td>{{ typeof val === 'object' ? JSON.stringify(val) : val }}</td>
							</tr>
						</tbody>
					</table>

					<!-- Permissions -->
					<table v-if="item.data?.permissions?.length" class="table table-sm alfred-perms-table">
						<thead><tr><th>Role</th><th>Read</th><th>Write</th><th>Create</th><th>Delete</th></tr></thead>
						<tbody>
							<tr v-for="p in item.data.permissions" :key="p.role">
								<td>{{ p.role }}</td>
								<td>{{ p.read ? '✓' : '' }}</td>
								<td>{{ p.write ? '✓' : '' }}</td>
								<td>{{ p.create ? '✓' : '' }}</td>
								<td>{{ p.delete ? '✓' : '' }}</td>
							</tr>
						</tbody>
					</table>
				</div>
			</div>

			<!-- Action buttons - only for Pending status -->
			<div v-if="changeset.status === 'Pending'" class="alfred-preview-actions">
				<button
					:class="['btn', 'btn-sm', changeset.dry_run_valid === 1 ? 'btn-success' : 'btn-warning']"
					@click="$emit('approve')">
					{{ changeset.dry_run_valid === 1 ? __("Approve & Deploy") : __("Deploy Anyway") }}
				</button>
				<button class="btn btn-default btn-sm" @click="$emit('modify')">{{ __("Request Changes") }}</button>
				<button class="btn btn-danger btn-sm" @click="$emit('reject')">{{ __("Reject") }}</button>
			</div>
		</div>
	</div>
</template>

<script setup>
import { computed } from "vue";

const props = defineProps({
	changeset: { type: Object, default: null },
	currentPhase: { type: String, default: null },
	deploySteps: { type: Array, default: () => [] },
	deployed: { type: Boolean, default: false },
});

defineEmits(["approve", "modify", "reject"]);

const PHASE_INFO = {
	requirement: { title: "Gathering Requirements...", desc: "Understanding your request and identifying what needs to be built." },
	assessment: { title: "Checking Feasibility...", desc: "Verifying permissions and checking for conflicts." },
	architecture: { title: "Designing Solution...", desc: "Planning DocTypes, fields, relationships, and scripts." },
	development: { title: "Generating Code...", desc: "Writing DocType definitions, Server Scripts, and Client Scripts." },
	testing: { title: "Validating...", desc: "Checking syntax, permissions, naming conflicts, and deployment order." },
	deployment: { title: "Preparing Deployment...", desc: "Building deployment plan for your approval." },
};

const phaseTitle = computed(() => PHASE_INFO[props.currentPhase]?.title || "Processing...");
const phaseDescription = computed(() => PHASE_INFO[props.currentPhase]?.desc || "");

const changes = computed(() => {
	let raw = props.changeset?.changes || [];
	if (typeof raw === "string") {
		try { raw = JSON.parse(raw); } catch { raw = []; }
	}
	return Array.isArray(raw) ? raw : [];
});

const dryRunIssues = computed(() => {
	const raw = props.changeset?.dry_run_issues;
	if (!raw) return [];
	if (Array.isArray(raw)) return raw;
	if (typeof raw === "string") {
		try { return JSON.parse(raw); } catch { return []; }
	}
	return [];
});

const groupedChanges = computed(() => {
	const groups = {};
	changes.value.forEach((c) => {
		const dt = c.doctype || c.data?.doctype || "Other";
		if (!groups[dt]) groups[dt] = [];
		groups[dt].push(c);
	});
	return groups;
});

// Fields to hide in generic key-value display (internal/noisy fields)
const HIDDEN_FIELDS = new Set(["doctype", "name", "owner", "creation", "modified", "modified_by", "idx", "docstatus"]);

function visibleFields(fields) {
	return (fields || []).filter((f) => !["Section Break", "Column Break", "Tab Break"].includes(f.fieldtype));
}

function displayableFields(data) {
	if (!data) return {};
	const result = {};
	for (const [key, val] of Object.entries(data)) {
		if (HIDDEN_FIELDS.has(key)) continue;
		if (val === null || val === undefined || val === "") continue;
		if (Array.isArray(val) && val.length === 0) continue;
		result[key] = val;
	}
	return result;
}

function docStatusLabel(status) {
	const n = Number(status || 0);
	if (n === 0) return "Draft (0)";
	if (n === 1) return "Submitted (1)";
	if (n === 2) return "Cancelled (2)";
	return `(${status})`;
}

function recipientsSummary(data) {
	const recipients = data?.recipients;
	if (!recipients || !Array.isArray(recipients)) return "";
	return recipients.map((r) => {
		if (r.receiver_by_document_field) return `Field: ${r.receiver_by_document_field}`;
		if (r.receiver_by_role) return `Role: ${r.receiver_by_role}`;
		if (r.cc) return `CC: ${r.cc}`;
		return JSON.stringify(r);
	}).join(", ");
}

function stepIcon(status) {
	return status === "success" ? "✓" : status === "in_progress" ? "⏳" : "✗";
}

function stepColor(status) {
	return status === "success" ? "var(--green-600)" : status === "in_progress" ? "var(--orange-600)" : "var(--red-600)";
}
</script>

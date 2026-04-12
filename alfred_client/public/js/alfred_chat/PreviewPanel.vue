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
					<table v-if="type === 'DocType' && item.data?.fields?.length" class="table table-sm alfred-fields-table">
						<thead><tr><th>Field</th><th>Type</th><th>Label</th><th>Required</th></tr></thead>
						<tbody>
							<tr v-for="f in visibleFields(item.data.fields)" :key="f.fieldname">
								<td><code>{{ f.fieldname }}</code></td>
								<td>{{ f.fieldtype }}</td>
								<td>{{ f.label }}</td>
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
							<tr v-if="item.data.script_type"><td class="text-muted">Type</td><td>{{ item.data.script_type }}</td></tr>
							<tr v-if="item.data.doctype_event"><td class="text-muted">Event</td><td>{{ item.data.doctype_event }}</td></tr>
						</tbody>
					</table>
					<pre v-if="item.data?.script" class="alfred-code-preview"><code>{{ item.data.script }}</code></pre>

					<!-- Client Script details -->
					<table v-if="type === 'Client Script' && item.data" class="table table-sm alfred-detail-table">
						<tbody>
							<tr v-if="item.data.dt"><td class="text-muted">DocType</td><td>{{ item.data.dt }}</td></tr>
							<tr v-if="item.data.view"><td class="text-muted">View</td><td>{{ item.data.view }}</td></tr>
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
						</tbody>
					</table>

					<!-- Workflow details -->
					<table v-if="type === 'Workflow' && item.data" class="table table-sm alfred-detail-table">
						<tbody>
							<tr v-if="item.data.document_type"><td class="text-muted">DocType</td><td>{{ item.data.document_type }}</td></tr>
							<tr v-if="item.data.is_active"><td class="text-muted">Active</td><td>Yes</td></tr>
						</tbody>
					</table>

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

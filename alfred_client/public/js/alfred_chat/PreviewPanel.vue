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
			<h5 class="alfred-preview-title">{{ __("Changeset Preview") }}</h5>
			<div class="alfred-preview-summary">
				{{ __("{0} operation(s) will be applied to your site", [changes.length]) }}
			</div>

			<div v-for="(items, type) in groupedChanges" :key="type" class="alfred-preview-group">
				<h6 class="alfred-preview-group-title">{{ type }}s ({{ items.length }})</h6>
				<div v-for="item in items" :key="item.data?.name" class="alfred-preview-item">
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

					<!-- Code preview for scripts -->
					<pre v-if="item.data?.script" class="alfred-code-preview"><code>{{ item.data.script }}</code></pre>

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

			<!-- Action buttons -->
			<div v-if="changeset.status === 'Pending'" class="alfred-preview-actions">
				<button class="btn btn-success btn-sm" @click="$emit('approve')">{{ __("Approve & Deploy") }}</button>
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

const changes = computed(() => props.changeset?.changes || []);

const groupedChanges = computed(() => {
	const groups = {};
	changes.value.forEach((c) => {
		const dt = c.doctype || c.data?.doctype || "Other";
		if (!groups[dt]) groups[dt] = [];
		groups[dt].push(c);
	});
	return groups;
});

function visibleFields(fields) {
	return (fields || []).filter((f) => !["Section Break", "Column Break", "Tab Break"].includes(f.fieldtype));
}

function stepIcon(status) {
	return status === "success" ? "✓" : status === "in_progress" ? "⏳" : "✗";
}

function stepColor(status) {
	return status === "success" ? "var(--green-600)" : status === "in_progress" ? "var(--orange-600)" : "var(--red-600)";
}
</script>

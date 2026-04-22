<template>
	<div class="alfred-preview" :data-preview-state="previewState">
		<!-- EMPTY: no changeset, no run in flight. -->
		<div v-if="previewState === 'EMPTY'" class="alfred-preview-empty-state">
			<div class="alfred-preview-hero">
				<div class="alfred-preview-mark" aria-hidden="true">&#9719;</div>
				<h3 class="alfred-preview-title-lg">{{ __("Preview Panel") }}</h3>
				<p class="alfred-preview-subtitle">{{ emptyMessage }}</p>
			</div>
			<div class="alfred-preview-hints">
				<div class="alfred-preview-hints-label">{{ __("What lands here") }}</div>
				<div class="alfred-preview-hint">
					<span class="alfred-preview-hint-icon" aria-hidden="true">&#9634;</span>
					<div>
						<strong>{{ __("Proposed changes") }}</strong>
						<p>{{ __("DocTypes, custom fields, notifications, scripts - each with a plain-English summary you can review.") }}</p>
					</div>
				</div>
				<div class="alfred-preview-hint">
					<span class="alfred-preview-hint-icon" aria-hidden="true">&#10003;</span>
					<div>
						<strong>{{ __("Approve, modify, or reject") }}</strong>
						<p>{{ __("Nothing touches your site until you click Approve and Deploy.") }}</p>
					</div>
				</div>
				<div class="alfred-preview-hint">
					<span class="alfred-preview-hint-icon" aria-hidden="true">&#8634;</span>
					<div>
						<strong>{{ __("Roll back anytime") }}</strong>
						<p>{{ __("Every deployment captures a rollback plan - one click undoes it.") }}</p>
					</div>
				</div>
			</div>
		</div>

		<!-- WORKING: run in flight, no changeset yet. Show last known phase. -->
		<div v-else-if="previewState === 'WORKING'" class="alfred-preview-working">
			<div class="alfred-preview-hero">
				<div class="alfred-preview-mark alfred-preview-mark-pulse" aria-hidden="true">&#9675;</div>
				<h3 class="alfred-preview-title-lg">{{ phaseTitle }}</h3>
				<p class="alfred-preview-subtitle">{{ phaseDescription || __("Alfred is working on it...") }}</p>
			</div>
			<div class="alfred-preview-steps" role="list">
				<div
					v-for="step in phaseSteps"
					:key="step.key"
					class="alfred-preview-step"
					:class="{
						'alfred-preview-step-current': step.current,
						'alfred-preview-step-done': step.done,
					}"
					role="listitem"
				>
					<span class="alfred-preview-step-dot" aria-hidden="true">
						{{ step.done ? '\u2713' : (step.current ? '\u25CF' : '\u25CB') }}
					</span>
					<span class="alfred-preview-step-label">{{ step.label }}</span>
				</div>
			</div>
		</div>

		<!-- VALIDATING: Approve clicked, second-pass dry-run in flight.
		     Hero mark keeps pulsing; info banner announces the second
		     pass; the changeset body is shown in a muted card so the
		     user understands this is the same payload being checked. -->
		<div v-else-if="previewState === 'VALIDATING'" class="alfred-preview-content">
			<div class="alfred-preview-status-row">
				<div class="alfred-mark alfred-mark--preview alfred-mark--sm alfred-mark-pulse" aria-hidden="true">&#9675;</div>
				<div class="alfred-banner alfred-banner--info">
					<span class="alfred-banner__icon" aria-hidden="true">&#9679;</span>
					<div class="alfred-banner__body">{{ __("Validating - checking the site one more time before deploy...") }}</div>
				</div>
			</div>
			<div v-if="changeset" class="alfred-card alfred-card--muted alfred-preview-content-body">
				<h5 class="alfred-preview-title">{{ __("Changeset Preview") }}</h5>
				<div class="alfred-preview-summary">{{ __("{0} operation(s)", [changes.length]) }}</div>
			</div>
		</div>

		<!-- DEPLOYING: live deploy-progress stream using the shared
		     .alfred-step vocabulary. Each row slides in as the stream
		     arrives (alfred-ticker-in keyframe). -->
		<div v-else-if="previewState === 'DEPLOYING'" class="alfred-preview-content">
			<div class="alfred-preview-status-row">
				<div class="alfred-mark alfred-mark--preview alfred-mark--sm alfred-mark-pulse" aria-hidden="true">&#9675;</div>
				<h5 class="alfred-preview-title-lg">{{ __("Deploying...") }}</h5>
			</div>
			<div class="alfred-preview-steps" role="list">
				<div
					v-for="step in deploySteps"
					:key="step.step"
					:class="[
						'alfred-step', 'alfred-step--stream',
						step.status === 'success' ? 'alfred-step--done' :
						step.status === 'failed' ? 'alfred-step--failed' :
						'alfred-step--current',
					]"
					role="listitem"
				>
					<span class="alfred-step-dot" aria-hidden="true">{{ stepIcon(step.status) }}</span>
					<span class="alfred-step-label">{{ __("Step") }} {{ step.step }}/{{ step.total }}: {{ step.name || step.doctype }}</span>
				</div>
				<div v-if="!deploySteps.length" class="alfred-step alfred-step--current">
					<span class="alfred-step-dot" aria-hidden="true">&#9679;</span>
					<span class="alfred-step-label">{{ __("Waiting for the first deploy step...") }}</span>
				</div>
			</div>
		</div>

		<!-- PENDING / DEPLOYED / ROLLED_BACK / FAILED / REJECTED / CANCELLED
		     all render the changeset body with a state-specific banner. -->
		<div v-else-if="changeset" class="alfred-preview-content">
			<!-- V2: Module context badge when alfred-processing detected a module -->
			<div v-if="detectedModuleDisplay" class="alfred-module-badge">
				<span class="alfred-module-badge__icon" aria-hidden="true">&#9675;</span>
				<span class="alfred-module-badge__label">
					{{ __("Module context:") }} <strong>{{ detectedModuleDisplay }}</strong>
				</span>
			</div>
			<!-- PENDING: validation banners (success if dry-run passed,
			     warn with issue list if not) -->
			<div v-if="previewState === 'PENDING' && changeset.dry_run_valid === 1" class="alfred-banner alfred-banner--success">
				<span class="alfred-banner__icon" aria-hidden="true">&#10003;</span>
				<div class="alfred-banner__body">{{ __("Validated - ready to deploy") }}</div>
			</div>
			<div v-else-if="previewState === 'PENDING' && dryRunIssues.length" class="alfred-banner alfred-banner--warn">
				<span class="alfred-banner__icon" aria-hidden="true">&#9888;</span>
				<div class="alfred-banner__body">
					<strong>{{ __("{0} validation issue(s) found - review before deploying", [dryRunIssues.length]) }}</strong>
					<ul class="alfred-banner__list">
						<li v-for="(issue, i) in dryRunIssues" :key="i" :class="`alfred-issue-${issue.severity || 'warning'}`">
							<strong>{{ (issue.severity || 'warning').toUpperCase() }}:</strong>
							<span v-if="issue.doctype"> {{ issue.doctype }}<span v-if="issue.name"> ({{ issue.name }})</span> - </span>
							{{ issue.issue || issue.message }}
						</li>
					</ul>
				</div>
			</div>
			<!-- V2: Module specialist validation notes -->
			<div
				v-if="previewState === 'PENDING' && moduleValidationNotes.length"
				class="alfred-banner alfred-banner--module-notes"
			>
				<span class="alfred-banner__icon" aria-hidden="true">&#9873;</span>
				<div class="alfred-banner__body">
					<strong>{{ __("{0} module convention note(s)", [moduleValidationNotes.length]) }}</strong>
					<ul class="alfred-banner__list">
						<li
							v-for="(note, i) in moduleValidationNotes"
							:key="i"
							:class="`alfred-module-note alfred-module-note--${note.severity || 'advisory'}`"
						>
							<strong>{{ (note.severity || 'advisory').toUpperCase() }}:</strong>
							{{ note.issue }}
							<span v-if="note.fix" class="alfred-module-note__fix">
								&#8594; {{ note.fix }}
							</span>
							<small class="alfred-module-note__source" v-if="note.source">
								({{ note.source }})
							</small>
						</li>
					</ul>
				</div>
			</div>

			<!-- Terminal-state banners: one banner per state with its
			     own tone so the three "red" states (FAILED crash vs
			     user-initiated ROLLED_BACK vs REJECTED) read as
			     visually distinct intents. -->
			<div v-if="previewState === 'DEPLOYED'" class="alfred-banner alfred-banner--success">
				<div class="alfred-mark alfred-mark--preview alfred-mark--sm" aria-hidden="true">&#10003;</div>
				<div class="alfred-banner__body"><strong>{{ __("Deployed successfully") }}</strong></div>
			</div>
			<div v-else-if="previewState === 'ROLLED_BACK'" class="alfred-banner alfred-banner--warn">
				<span class="alfred-banner__icon" aria-hidden="true">&#8634;</span>
				<div class="alfred-banner__body">{{ __("Deployment rolled back") }}</div>
			</div>
			<div v-else-if="previewState === 'FAILED'" class="alfred-banner alfred-banner--danger">
				<span class="alfred-banner__icon" aria-hidden="true">&#10007;</span>
				<div class="alfred-banner__body"><strong>{{ failureHeadline }}</strong></div>
			</div>
			<div v-else-if="previewState === 'REJECTED'" class="alfred-banner alfred-banner--neutral">
				<span class="alfred-banner__icon" aria-hidden="true">&#10007;</span>
				<div class="alfred-banner__body">{{ __("Changeset rejected") }}</div>
			</div>
			<div v-else-if="previewState === 'CANCELLED'" class="alfred-banner alfred-banner--warn">
				<span class="alfred-banner__icon" aria-hidden="true">&#9888;</span>
				<div class="alfred-banner__body">{{ __("Run cancelled") }}</div>
			</div>

			<!-- FAILED: brief excerpt of the failed deployment_log steps. -->
			<div v-if="previewState === 'FAILED' && failedSteps.length" class="alfred-preview-steps" role="list">
				<div class="alfred-eyebrow alfred-preview-steps-label">{{ __("Failed deploy steps") }}</div>
				<div
					v-for="step in failedSteps"
					:key="step.step || step.index"
					class="alfred-step alfred-step--failed"
					role="listitem"
				>
					<span class="alfred-step-dot" aria-hidden="true">{{ stepIcon(step.status || 'failed') }}</span>
					<span class="alfred-step-label">{{ __("Step") }} {{ step.step || step.index }}: {{ step.name || step.doctype }} - {{ step.error || step.message || __("failed") }}</span>
				</div>
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

					<!-- Plain-English description card -->
					<div class="alfred-describe-card" :class="`alfred-describe-${describe(type, item).tone}`">
						<div class="alfred-describe-what">
							<span class="alfred-describe-icon">{{ describe(type, item).icon }}</span>
							<span>{{ describe(type, item).what }}</span>
						</div>
						<div v-if="describe(type, item).when" class="alfred-describe-row">
							<span class="alfred-describe-label">When:</span>
							<span>{{ describe(type, item).when }}</span>
						</div>
						<div v-if="describe(type, item).who" class="alfred-describe-row">
							<span class="alfred-describe-label">Affects:</span>
							<span>{{ describe(type, item).who }}</span>
						</div>
						<div v-if="describe(type, item).impact" class="alfred-describe-row">
							<span class="alfred-describe-label">Effect:</span>
							<span>{{ describe(type, item).impact }}</span>
						</div>
						<div v-if="describe(type, item).warning" class="alfred-describe-warning">
							<span>&#9888; {{ describe(type, item).warning }}</span>
						</div>
					</div>

					<details class="alfred-details-expand">
						<summary class="alfred-details-summary">{{ __("Technical details") }}</summary>

					<!-- Fields table for DocTypes -->
					<!--
						When Alfred's per-intent DocType Builder has populated
						field_defaults_meta, render every shape-defining field
						unconditionally with a "default" pill on rows the LLM
						(or the defaults-backfill post-processor) sourced from
						the registry. Hover the pill for the rationale.

						If field_defaults_meta is absent, fall back to the
						legacy v-if-on-truthy rendering - keeps non-DocType-
						Builder changesets displaying the same way.
					-->
					<div v-if="type === 'DocType' && item.field_defaults_meta" class="alfred-defaults-banner">
						{{ __("Alfred filled the fields below using sensible defaults where you didn't specify one. If any look wrong, say so in your next message.") }}
					</div>
					<table v-if="type === 'DocType' && item.data && item.field_defaults_meta" class="table table-sm alfred-detail-table">
						<tbody>
							<tr v-for="key in DOCTYPE_REGISTRY_KEYS" :key="key">
								<td class="text-muted">
									{{ DOCTYPE_REGISTRY_LABELS[key] }}
									<span
										v-if="isDefaulted(item, key)"
										class="alfred-default-pill"
										:title="defaultRationale(item, key) || ''"
									>{{ __("default") }}</span>
								</td>
								<td>
									<span v-if="key === 'permissions'">{{ permissionsSummary(item.data[key]) }}</span>
									<code v-else-if="key === 'autoname'">{{ item.data[key] }}</code>
									<span v-else>{{ fieldDisplayValue(item.data[key], key) }}</span>
								</td>
							</tr>
						</tbody>
					</table>
					<table v-else-if="type === 'DocType' && item.data" class="table table-sm alfred-detail-table">
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
					</details>
				</div>
			</div>

			<!-- PENDING: Approve / Request Changes / Reject. -->
			<div v-if="previewState === 'PENDING'" class="alfred-preview-actions">
				<button
					:class="[
						changeset.dry_run_valid === 1 && !hasModuleBlocker
							? 'alfred-btn-primary alfred-btn-primary--success'
							: 'alfred-btn-primary alfred-btn-primary--warn',
					]"
					:disabled="hasModuleBlocker"
					:title="hasModuleBlocker ? __('Blocker-severity module note prevents deploy - address or rephrase and retry.') : ''"
					@click="$emit('approve')">
					{{ hasModuleBlocker
						? __("Deploy blocked")
						: (changeset.dry_run_valid === 1 ? __("Approve & Deploy") : __("Deploy Anyway")) }}
				</button>
				<button class="alfred-btn-ghost" @click="$emit('modify')">{{ __("Request Changes") }}</button>
				<button class="alfred-btn-ghost alfred-btn-ghost--danger" @click="$emit('reject')">{{ __("Reject") }}</button>
			</div>

			<!-- DEPLOYED: Rollback button if rollback_data exists. -->
			<div v-else-if="previewState === 'DEPLOYED' && hasRollbackData" class="alfred-preview-actions">
				<button
					class="alfred-btn-ghost"
					:disabled="rollbackInFlight"
					@click="$emit('rollback')">
					<span v-if="rollbackInFlight" class="alfred-btn-spinner" aria-hidden="true"></span>
					{{ rollbackInFlight ? __("Rolling back...") : __("Rollback") }}
				</button>
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
	// deployed is retained for backwards-compat but now derives from
	// changeset.status; callers can stop passing it once everything has
	// migrated to the new previewState contract.
	deployed: { type: Boolean, default: false },
	isProcessing: { type: Boolean, default: false },
	conversationStatus: { type: String, default: "" },
	// True while the second-pass dry-run after Approve is in flight.
	// Drives the VALIDATING state so the Approve button is hidden and a
	// neutral "re-validating" banner shows instead.
	validating: { type: Boolean, default: false },
	// True while a rollback call is in flight so the button label flips
	// to "Rolling back..." and is disabled.
	rollbackInFlight: { type: Boolean, default: false },
});

defineEmits(["approve", "modify", "reject", "rollback"]);

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

// Ordered step list for the WORKING-state pipeline visualization. Each
// step is marked done / current / pending based on the agent's current
// phase, so the user sees an always-advancing progress trail rather than
// a static "Alfred is working" line.
const PHASE_ORDER = [
	{ key: "requirement", label: __("Gathering requirements") },
	{ key: "assessment", label: __("Checking feasibility") },
	{ key: "architecture", label: __("Designing solution") },
	{ key: "development", label: __("Generating code") },
	{ key: "testing", label: __("Validating") },
	{ key: "deployment", label: __("Preparing deployment") },
];
const phaseSteps = computed(() => {
	const current = props.currentPhase;
	const idx = PHASE_ORDER.findIndex((s) => s.key === current);
	return PHASE_ORDER.map((s, i) => ({
		...s,
		// If we haven't seen a recognised phase yet, mark the first as current
		// so the trail still has a focal point instead of six dim dots.
		done: idx >= 0 && i < idx,
		current: idx >= 0 ? i === idx : i === 0,
	}));
});

// ── State machine ──────────────────────────────────────────────
//
// The preview panel renders one of ten states. Each is reached via a
// pure function of the incoming props, so the UI is a deterministic
// projection of (changeset, conversationStatus, deploySteps, isProcessing,
// validating) and can be unit-tested one state at a time.
//
// Priority order when conditions overlap:
//   VALIDATING beats DEPLOYING beats changeset-based states so the
//   Approve-click visual feedback is not overridden by the pending
//   changeset still sitting in memory.
const previewState = computed(() => {
	// Validating: Approve was just clicked, dry-run is running.
	if (props.validating) return "VALIDATING";

	// Deploying: either the status is Approved/Deploying OR deploy
	// progress events are currently streaming in.
	const cs = props.changeset;
	const status = cs?.status;
	const streamingDeploy = Array.isArray(props.deploySteps) && props.deploySteps.length > 0;
	if (status === "Approved" || status === "Deploying") return "DEPLOYING";
	if (streamingDeploy && (!status || status === "Pending")) return "DEPLOYING";

	// Changeset-based terminal states.
	if (status === "Deployed") return "DEPLOYED";
	if (status === "Rolled Back") {
		// Distinguish a user-initiated rollback from an auto-rollback
		// triggered by a mid-deploy failure. Auto-rollback leaves a
		// failed step in deployment_log; user-initiated rollback has
		// only successful deploy steps followed by rollback entries.
		return deploymentLogHasFailure.value ? "FAILED" : "ROLLED_BACK";
	}
	if (status === "Rejected") return "REJECTED";
	if (status === "Pending") return "PENDING";

	// No changeset: look to conversation context.
	if (props.conversationStatus === "Cancelled") return "CANCELLED";
	if (props.isProcessing) return "WORKING";
	return "EMPTY";
});

const deploymentLog = computed(() => {
	const raw = props.changeset?.deployment_log;
	if (!raw) return [];
	if (Array.isArray(raw)) return raw;
	if (typeof raw === "string") {
		try { return JSON.parse(raw); } catch { return []; }
	}
	return [];
});

const deploymentLogHasFailure = computed(() =>
	deploymentLog.value.some((s) => (s?.status || "").toLowerCase() === "failed")
);

const failedSteps = computed(() =>
	deploymentLog.value.filter((s) => (s?.status || "").toLowerCase() === "failed")
);

const hasRollbackData = computed(() => {
	const raw = props.changeset?.rollback_data;
	if (!raw) return false;
	if (Array.isArray(raw)) return raw.length > 0;
	if (typeof raw === "string") {
		try { return Array.isArray(JSON.parse(raw)) && JSON.parse(raw).length > 0; } catch { return false; }
	}
	return false;
});

const emptyMessage = computed(() => {
	if (props.conversationStatus === "Cancelled") {
		return __("Run cancelled. Send a new prompt to continue.");
	}
	if (props.conversationStatus === "Completed") {
		return __("Conversation complete. Send a new prompt to start another task.");
	}
	if (props.conversationStatus === "Failed") {
		return __("The previous run failed. Send a new prompt to try again.");
	}
	return __("Changes proposed by Alfred will appear here for your review.");
});

const failureHeadline = computed(() => {
	const failed = failedSteps.value[0];
	if (failed) {
		const ref = failed.name || failed.doctype || "";
		return ref
			? __("Deploy failed at {0} - rolled back", [ref])
			: __("Deploy failed - rolled back");
	}
	return __("Deploy failed - rolled back");
});

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

// V2: module specialist validation notes. Only populated when
// alfred-processing runs with ALFRED_MODULE_SPECIALISTS=1 and a module was
// detected for the prompt. Shape mirrors dry_run_issues plus source/fix
// fields. Empty when flag off or no module.
const moduleValidationNotes = computed(() => {
	const raw = props.changeset?.module_validation_notes;
	if (!raw) return [];
	if (Array.isArray(raw)) return raw;
	if (typeof raw === "string") {
		try { return JSON.parse(raw); } catch { return []; }
	}
	return [];
});

const detectedModuleDisplay = computed(() => {
	return props.changeset?.detected_module || "";
});

// V2: any blocker-severity module note gates the Deploy button. This is
// the "load-bearing" severity - blockers describe something that will
// break at deploy time (invalid hook wiring, missing required module
// field, etc). User must either edit the prompt to address the blocker
// or explicitly override by choosing to deploy anyway (button disabled
// by default; reject/modify still available).
const hasModuleBlocker = computed(() =>
	moduleValidationNotes.value.some((n) => (n.severity || "") === "blocker")
);

const groupedChanges = computed(() => {
	const groups = {};
	changes.value.forEach((c) => {
		const dt = c.doctype || c.data?.doctype || "Other";
		if (!groups[dt]) groups[dt] = [];
		groups[dt].push(c);
	});
	return groups;
});

// ── Per-intent Builder defaults review (DocType) ──────────────────
//
// When alfred-processing runs with ALFRED_PER_INTENT_BUILDERS=1 and the
// prompt is classified as create_doctype, each ChangesetItem gains a
// field_defaults_meta dict recording which shape-defining fields were
// sourced from the user vs. from the intent registry (with a rationale
// for each defaulted field). See
// docs/specs/2026-04-21-doctype-builder-specialist.md in the
// alfred-processing repo for the full shape.
//
// The registry lives in alfred-processing; these arrays mirror the
// create_doctype registry's `fields[].key` / `.label` so the UI can
// render the table order and labels without fetching the registry.
const DOCTYPE_REGISTRY_KEYS = [
	"module",
	"is_submittable",
	"autoname",
	"istable",
	"issingle",
	"permissions",
];
const DOCTYPE_REGISTRY_LABELS = {
	module: __("Module"),
	is_submittable: __("Submittable?"),
	autoname: __("Naming rule"),
	istable: __("Child table?"),
	issingle: __("Singleton?"),
	permissions: __("Permissions"),
};

function isDefaulted(item, key) {
	return item?.field_defaults_meta?.[key]?.source === "default";
}

function defaultRationale(item, key) {
	return item?.field_defaults_meta?.[key]?.rationale || "";
}

function fieldDisplayValue(value, key) {
	if (value === null || value === undefined || value === "") return __("(empty)");
	// check-typed registry fields store 0/1 in Frappe; show Yes/No for readability
	if (["is_submittable", "istable", "issingle"].includes(key)) {
		return value ? __("Yes") : __("No");
	}
	return String(value);
}

function permissionsSummary(perms) {
	if (!Array.isArray(perms) || perms.length === 0) return __("(none)");
	return perms.map((p) => p.role || "(unnamed)").join(", ");
}

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

// ── describe(): plain-English summary of a changeset item ───────────
//
// Returns { what, when, who, impact, warning, icon, tone } where:
//   what    - one-line human-readable description of what the change does
//   when    - when it fires / takes effect (optional, for triggers)
//   who     - who/what it affects (optional, for scoped changes)
//   impact  - business outcome the user cares about (optional)
//   warning - caveat the user should know about (optional)
//   icon    - single emoji-like char for the description card
//   tone    - "info" | "warn" | "danger" for styling
//
// The goal is that a user reviewing the preview panel can read one sentence
// per item and understand what they're approving, without having to read
// the Python / Jinja / field tables underneath.

const SERVER_SCRIPT_EVENT_LABELS = {
	before_insert: "when a new record is about to be created",
	after_insert: "right after a new record is created",
	before_save: "before every save",
	after_save: "right after every save",
	before_submit: "when a user tries to submit the document",
	on_submit: "right after the document is submitted",
	before_cancel: "when a user tries to cancel a submitted document",
	on_cancel: "right after the document is cancelled",
	before_validate: "during the validation step",
	validate: "during the validation step",
	on_trash: "when the document is being deleted",
	after_delete: "after the document is deleted",
	on_update: "on every update to the document",
};

const NOTIFICATION_EVENT_LABELS = {
	"New": "a new record is created",
	"Save": "the document is saved (create or update)",
	"Submit": "the document is submitted",
	"Cancel": "the document is cancelled",
	"Value Change": "a specific field value changes",
	"Days Before": "a number of days before a date field",
	"Days After": "a number of days after a date field",
	"Method": "a custom method fires",
};

function _extractThrowMessage(script) {
	// Find the first `frappe.throw("...")` or `frappe.throw('...')` and
	// return the message. Best-effort; returns empty string if no throw.
	if (!script || typeof script !== "string") return "";
	const match = script.match(/frappe\.throw\s*\(\s*(['"`])([^'"`\n]{1,200})\1/);
	return match ? match[2] : "";
}

function _classifyScriptIntent(script) {
	// Classify what a Server Script does based on keywords. Returns one
	// of "validation", "notification", "auto_update", "audit_log",
	// "integration", or "custom".
	if (!script || typeof script !== "string") return "custom";
	const s = script.toLowerCase();
	if (s.includes("frappe.throw") || s.includes("frappe.validationerror")) {
		return "validation";
	}
	if (s.includes("frappe.sendmail") || s.includes("frappe.send_email")) {
		return "notification";
	}
	if (s.includes("frappe.db.set_value") || s.includes("doc.db_set(")) {
		return "auto_update";
	}
	if (s.includes("alfred audit") || s.includes("audit_log")) {
		return "audit_log";
	}
	if (s.includes("requests.") || s.includes("urllib") || s.includes("http.client")) {
		return "integration";
	}
	return "custom";
}

function _describeServerScript(data) {
	const ref = data?.reference_doctype || "<unknown DocType>";
	const scriptType = data?.script_type || "DocType Event";
	const event = data?.doctype_event || "";
	const script = data?.script || "";
	const throwMsg = _extractThrowMessage(script);
	const intent = _classifyScriptIntent(script);

	if (scriptType === "DocType Event") {
		const whenPhrase = SERVER_SCRIPT_EVENT_LABELS[event] || `on \`${event}\``;
		if (intent === "validation") {
			return {
				what: `Adds a validation rule to **${ref}**.`,
				when: `Runs ${whenPhrase}.`,
				who: `Every ${ref} record.`,
				impact: throwMsg
					? `Blocks the save with the message: "${throwMsg}"`
					: "Blocks the save if the script's condition is not met.",
				icon: "⛔",
				tone: "warn",
			};
		}
		if (intent === "auto_update") {
			return {
				what: `Automatically updates fields on **${ref}**.`,
				when: `Runs ${whenPhrase}.`,
				who: `Every ${ref} record.`,
				impact: "Fields are written server-side; users won't see a prompt.",
				icon: "⚡",
				tone: "info",
			};
		}
		if (intent === "notification") {
			return {
				what: `Sends a programmatic email when **${ref}** events fire.`,
				when: `Runs ${whenPhrase}.`,
				who: `Recipients determined inside the script.`,
				icon: "✉",
				tone: "info",
			};
		}
		if (intent === "audit_log") {
			return {
				what: `Writes an audit log entry for **${ref}** changes.`,
				when: `Runs ${whenPhrase}.`,
				who: `Every ${ref} record.`,
				icon: "📜",
				tone: "info",
			};
		}
		return {
			what: `Runs custom server-side logic on **${ref}**.`,
			when: `Fires ${whenPhrase}.`,
			icon: "⚙",
			tone: "info",
		};
	}
	if (scriptType === "API") {
		return {
			what: `Exposes a custom API endpoint: \`${data?.api_method || "<method>"}\`.`,
			when: "Runs when a client calls the endpoint.",
			who: "Users with permission to call the method.",
			icon: "🔌",
			tone: "info",
		};
	}
	if (scriptType === "Scheduler Event") {
		return {
			what: `Runs a scheduled background job.`,
			when: `Fires on \`${data?.event_frequency || data?.cron_format || "scheduled cron"}\`.`,
			icon: "⏰",
			tone: "info",
		};
	}
	if (scriptType === "Permission Query") {
		return {
			what: `Customises the list-view permission filter for **${ref}**.`,
			impact: "Users will only see records the script allows.",
			icon: "🔒",
			tone: "warn",
		};
	}
	return {
		what: `Adds a Server Script of type ${scriptType}.`,
		icon: "⚙",
		tone: "info",
	};
}

function _describeNotification(data) {
	const doc = data?.document_type || "<target DocType>";
	const ev = data?.event || "";
	const channel = data?.channel || "Email";
	const whenPhrase = NOTIFICATION_EVENT_LABELS[ev] || (ev ? `on \`${ev}\`` : "");
	const recipients = recipientsSummary(data) || "the recipients configured below";
	return {
		what: `Sends a ${channel} notification for **${doc}** events.`,
		when: whenPhrase ? `Fires when ${whenPhrase}.` : undefined,
		who: `Delivered to: ${recipients}.`,
		impact: data?.subject ? `Subject: "${data.subject}"` : undefined,
		warning: data?.enabled === 0 ? "This notification will be created DISABLED." : undefined,
		icon: "✉",
		tone: "info",
	};
}

function _describeCustomField(data) {
	const target = data?.dt || "<target DocType>";
	const fieldname = data?.fieldname || "<fieldname>";
	const fieldtype = data?.fieldtype || "Data";
	const label = data?.label || fieldname;
	const optionsNote = data?.options
		? ` Options: ${String(data.options).replace(/\n/g, " / ").slice(0, 80)}`
		: "";
	const reqd = data?.reqd ? " Required." : "";
	return {
		what: `Adds a new **${fieldtype}** field \`${fieldname}\` (label: "${label}") to **${target}**.`,
		who: `Visible on every ${target} form.${reqd}`,
		impact: `Existing records get NULL for this field until edited.${optionsNote}`,
		warning: data?.reqd ? "Required fields can block save on existing records that lack a value." : undefined,
		icon: "＋",
		tone: "info",
	};
}

function _describeWorkflow(data) {
	const doc = data?.document_type || "<target DocType>";
	const stateField = data?.workflow_state_field || "workflow_state";
	const states = Array.isArray(data?.states) ? data.states : [];
	const transitions = Array.isArray(data?.transitions) ? data.transitions : [];
	const stateNames = states.map((s) => s.state).filter(Boolean).join(" → ");
	return {
		what: `Sets up a workflow on **${doc}** with ${states.length} state(s).`,
		when: `Activated on creation of this Workflow definition.`,
		who: `Every existing and future ${doc} record.`,
		impact: stateNames
			? `State flow: ${stateNames}. ${transitions.length} transition(s) defined. State field: \`${stateField}\`.`
			: `${transitions.length} transition(s) defined. State field: \`${stateField}\`.`,
		warning: data?.is_active === 0
			? "This workflow will be created INACTIVE."
			: "Existing records will immediately be subject to this workflow's transitions.",
		icon: "↻",
		tone: "warn",
	};
}

function _describeDocType(data) {
	const name = data?.name || "<DocType name>";
	const module = data?.module || "Alfred";
	const fields = Array.isArray(data?.fields) ? data.fields.filter(
		(f) => !["Section Break", "Column Break", "Tab Break"].includes(f.fieldtype)
	) : [];
	const fieldNames = fields.slice(0, 5).map((f) => f.fieldname).join(", ");
	const extra = fields.length > 5 ? ` (+${fields.length - 5} more)` : "";
	const flags = [];
	if (data?.is_submittable) flags.push("submittable");
	if (data?.is_tree) flags.push("tree");
	if (data?.is_single) flags.push("singleton");
	const flagNote = flags.length ? ` [${flags.join(", ")}]` : "";
	return {
		what: `Creates a new DocType **${name}** in the **${module}** module${flagNote}.`,
		who: `A new database table \`tab${name}\` will be created.`,
		impact: fields.length
			? `Fields: ${fieldNames}${extra}. Naming: ${data?.naming_rule || data?.autoname || "default"}.`
			: "No fields defined yet.",
		warning: data?.is_submittable
			? "Submittable DocTypes have a docstatus workflow (Draft → Submitted → Cancelled)."
			: undefined,
		icon: "▢",
		tone: "info",
	};
}

function _describeClientScript(data) {
	const target = data?.dt || "<target DocType>";
	const view = data?.view || "Form";
	return {
		what: `Adds client-side JavaScript to the **${target}** ${view} view.`,
		when: `Runs in the browser when the user opens or interacts with a ${target}.`,
		who: `Every user who opens this ${target} view.`,
		warning: data?.enabled === 0 ? "This client script will be created DISABLED." : undefined,
		icon: "⚛",
		tone: "info",
	};
}

function _describePropertySetter(data) {
	const doc = data?.doc_type || "<DocType>";
	const field = data?.field_name || "<field>";
	const prop = data?.property || "<property>";
	const val = data?.value;
	return {
		what: `Changes the \`${prop}\` property of \`${field}\` on **${doc}**.`,
		impact: val !== undefined ? `New value: \`${val}\`.` : undefined,
		icon: "⚙",
		tone: "info",
	};
}

function describe(type, item) {
	const data = item?.data || {};
	const op = item?.op || item?.operation || "create";
	let out;
	switch (type) {
		case "Server Script":
			out = _describeServerScript(data);
			break;
		case "Notification":
			out = _describeNotification(data);
			break;
		case "Custom Field":
			out = _describeCustomField(data);
			break;
		case "Workflow":
			out = _describeWorkflow(data);
			break;
		case "DocType":
			out = _describeDocType(data);
			break;
		case "Client Script":
			out = _describeClientScript(data);
			break;
		case "Property Setter":
			out = _describePropertySetter(data);
			break;
		default:
			out = {
				what: `${op === "create" ? "Creates" : op === "update" ? "Updates" : op} a ${type}.`,
				icon: "•",
				tone: "info",
			};
	}
	// For update/delete ops, prefix the `what` so users see the op explicitly
	if (op !== "create" && out.what && !out.what.toLowerCase().startsWith(op.toLowerCase())) {
		const verb = op === "update" ? "Updates" : op === "delete" ? "DELETES" : op;
		out.what = `${verb}: ${out.what}`;
		if (op === "delete") {
			out.tone = "danger";
			out.warning = (out.warning ? out.warning + " " : "") + "This will remove the record and cannot be undone without rollback data.";
		}
	}
	return out;
}

function stepIcon(status) {
	return status === "success" ? "✓" : status === "in_progress" ? "⏳" : "✗";
}

function stepColor(status) {
	return status === "success" ? "var(--green-600)" : status === "in_progress" ? "var(--orange-600)" : "var(--red-600)";
}
</script>

<style scoped>
/* ── Per-intent Builder defaults review ───────────────────────────
 * When alfred-processing annotates a DocType changeset item with
 * field_defaults_meta, the technical-details table renders every
 * shape-defining field with a "default" pill on rows whose value was
 * filled from the intent registry rather than the user. Hover the pill
 * to see the registry rationale. */
.alfred-defaults-banner {
	background: #f0f7ff;
	border: 1px solid #cfe3ff;
	border-radius: 4px;
	padding: 8px 12px;
	margin: 8px 0;
	font-size: 12px;
	color: #334;
}
.alfred-default-pill {
	display: inline-block;
	background: #eef;
	color: #334;
	font-size: 10px;
	font-weight: 500;
	text-transform: lowercase;
	padding: 1px 6px;
	border-radius: 10px;
	margin-left: 6px;
	cursor: help;
	vertical-align: middle;
}
.alfred-default-pill:hover { background: #dde; }

/* V2 Module Specialists: module badge + validation notes */
.alfred-module-badge {
	display: inline-flex;
	align-items: center;
	gap: 6px;
	padding: 4px 10px;
	margin: 6px 0 10px;
	background: #f5f7fb;
	border: 1px solid #d7dde9;
	border-radius: 12px;
	font-size: 12px;
	color: #334;
}
.alfred-banner--module-notes {
	background: #fff7e6;
	border: 1px solid #ffdfa3;
}
.alfred-module-note--advisory { color: #444; }
.alfred-module-note--warning { color: #8a5a00; }
.alfred-module-note--blocker { color: #a11; font-weight: 500; }
.alfred-module-note__fix {
	display: block;
	margin-top: 2px;
	color: #556;
	font-size: 11px;
}
.alfred-module-note__source {
	margin-left: 6px;
	color: #889;
	font-size: 10px;
}

/* ── Empty / Working hero states ───────────────────────────────────
 * Matches the chat-side empty-state language: gradient mark, centered
 * title + subtitle. Different hue (teal -> violet) so the two panels
 * feel related but distinct at a glance. */
.alfred-preview-empty-state,
.alfred-preview-working {
	display: flex; flex-direction: column; align-items: center;
	justify-content: center; gap: 28px; padding: 32px 16px;
	min-height: 100%; text-align: center;
}
.alfred-preview-hero { display: flex; flex-direction: column; align-items: center; gap: 10px; }
.alfred-preview-mark {
	width: 56px; height: 56px; border-radius: 16px;
	display: flex; align-items: center; justify-content: center;
	font-size: 26px; font-weight: 700; color: white;
	background: linear-gradient(135deg, #14b8a6, #7c3aed);
	box-shadow: 0 6px 18px rgba(20, 184, 166, 0.22);
	line-height: 1;
}
.alfred-preview-mark-pulse {
	animation: alfred-preview-pulse 1.6s ease-in-out infinite;
}
@keyframes alfred-preview-pulse {
	0%, 100% { box-shadow: 0 6px 18px rgba(20, 184, 166, 0.22); transform: scale(1); }
	50%      { box-shadow: 0 8px 26px rgba(124, 58, 237, 0.38); transform: scale(1.05); }
}
.alfred-preview-title-lg {
	margin: 4px 0 0; font-size: 18px; font-weight: 600;
	color: var(--text-color, #333);
}
.alfred-preview-subtitle {
	margin: 0; max-width: 420px; font-size: 13px;
	color: var(--text-muted, #6b7280); line-height: 1.5;
}

/* EMPTY hints: three rows describing what the panel is for. Uses the same
 * pill-card shape as the chat's starter prompts but non-interactive. */
.alfred-preview-hints {
	display: flex; flex-direction: column; gap: 10px;
	width: 100%; max-width: 480px; text-align: left;
}
.alfred-preview-hints-label {
	font-size: 11px; text-transform: uppercase; letter-spacing: 0.6px;
	color: var(--text-muted, #9ca3af); margin-bottom: 2px;
	padding-left: 4px;
}
.alfred-preview-hint {
	display: flex; gap: 12px; padding: 12px 14px;
	background: var(--bg-color, white);
	border: 1px solid var(--border-color, #e2e2e2); border-radius: 10px;
	font-size: 13px; line-height: 1.5;
}
.alfred-preview-hint strong { display: block; color: var(--text-color, #333); margin-bottom: 2px; }
.alfred-preview-hint p { margin: 0; color: var(--text-muted, #6b7280); font-size: 12px; }
.alfred-preview-hint-icon {
	flex-shrink: 0; width: 28px; height: 28px; border-radius: 8px;
	display: flex; align-items: center; justify-content: center;
	font-size: 14px; color: var(--blue-600, #2563eb);
	background: var(--blue-50, #eff6ff);
}

/* WORKING pipeline: ordered step list with current highlighted. */
.alfred-preview-steps {
	display: flex; flex-direction: column; gap: 8px;
	width: 100%; max-width: 360px; text-align: left;
	padding: 8px 0;
}
.alfred-preview-step {
	display: flex; align-items: center; gap: 10px;
	padding: 8px 12px; border-radius: 8px;
	font-size: 13px; color: var(--text-muted, #9ca3af);
	background: transparent;
	transition: background 0.2s, color 0.2s;
}
.alfred-preview-step-dot {
	display: inline-flex; align-items: center; justify-content: center;
	width: 18px; height: 18px; font-size: 11px; flex-shrink: 0;
}
.alfred-preview-step-done {
	color: var(--green-600, #059669);
}
.alfred-preview-step-done .alfred-preview-step-dot {
	color: var(--green-600, #059669);
}
.alfred-preview-step-current {
	color: var(--text-color, #111827); font-weight: 600;
	background: var(--blue-50, #eff6ff);
}
.alfred-preview-step-current .alfred-preview-step-dot {
	color: var(--blue-600, #2563eb);
	animation: alfred-step-pulse 1.2s ease-in-out infinite;
}
@keyframes alfred-step-pulse {
	0%, 100% { opacity: 1; transform: scale(1); }
	50%      { opacity: 0.6; transform: scale(1.15); }
}

/* Plain-English description card (one per changeset item) */
.alfred-describe-card {
	margin: 10px 0;
	padding: 12px 14px;
	border-radius: 6px;
	border-left: 3px solid var(--blue-500, #3b82f6);
	background: var(--bg-secondary, #f9fafb);
	font-size: 13px;
	line-height: 1.5;
}

.alfred-describe-info {
	border-left-color: var(--blue-500, #3b82f6);
	background: var(--bg-light-blue, #eff6ff);
}

.alfred-describe-warn {
	border-left-color: var(--orange-500, #f59e0b);
	background: var(--bg-light-orange, #fffbeb);
}

.alfred-describe-danger {
	border-left-color: var(--red-500, #ef4444);
	background: var(--bg-light-red, #fef2f2);
}

.alfred-describe-what {
	display: flex;
	align-items: flex-start;
	gap: 8px;
	font-weight: 500;
	color: var(--text-color, #111827);
	margin-bottom: 6px;
}

.alfred-describe-icon {
	font-size: 16px;
	line-height: 1;
	flex-shrink: 0;
	margin-top: 1px;
}

.alfred-describe-row {
	display: flex;
	gap: 6px;
	margin-top: 3px;
	color: var(--text-color, #374151);
}

.alfred-describe-label {
	font-weight: 600;
	color: var(--text-muted, #6b7280);
	min-width: 56px;
	flex-shrink: 0;
}

.alfred-describe-warning {
	margin-top: 8px;
	padding-top: 8px;
	border-top: 1px solid rgba(0, 0, 0, 0.08);
	color: var(--orange-700, #b45309);
	font-weight: 500;
}

/* Expandable technical details section - hides the field tables and
   code blocks behind a toggle so the description card is the primary
   thing the user reads, not the JSON dump. */
.alfred-details-expand {
	margin-top: 10px;
	border-top: 1px dashed var(--border-color, #e5e7eb);
	padding-top: 8px;
}

.alfred-details-summary {
	cursor: pointer;
	font-size: 11px;
	font-weight: 600;
	text-transform: uppercase;
	letter-spacing: 0.5px;
	color: var(--text-muted, #6b7280);
	user-select: none;
	padding: 4px 0;
}

.alfred-details-summary:hover {
	color: var(--text-color, #111827);
}

.alfred-details-expand[open] .alfred-details-summary {
	color: var(--text-color, #111827);
	margin-bottom: 6px;
}
</style>

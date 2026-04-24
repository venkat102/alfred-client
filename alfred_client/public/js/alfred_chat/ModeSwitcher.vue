<template>
	<div class="alfred-mode-switcher" role="group" :aria-label="__('Chat mode')">
		<button
			v-for="opt in options"
			:key="opt.value"
			type="button"
			:class="[
				'alfred-mode-btn',
				`alfred-mode-btn-${opt.value}`,
				{ 'alfred-mode-btn-active': modelValue === opt.value },
			]"
			:title="opt.tooltip"
			:aria-pressed="modelValue === opt.value"
			:data-testid="`alfred-mode-${opt.value}`"
			@click="$emit('update:modelValue', opt.value)"
		>
			<!-- Inline SVG icon per mode. Uses currentColor so the per-mode
			     active-state colors propagate automatically. SVG is used
			     instead of Font Awesome because the fa-* font may not be
			     loaded on this page bundle, which made earlier icon-only
			     buttons render as empty boxes. -->
			<svg
				class="alfred-mode-icon"
				xmlns="http://www.w3.org/2000/svg"
				width="12"
				height="12"
				viewBox="0 0 24 24"
				fill="none"
				stroke="currentColor"
				stroke-width="2"
				stroke-linecap="round"
				stroke-linejoin="round"
				aria-hidden="true"
				v-html="opt.svg"
			/>
			<span class="alfred-mode-label">{{ opt.label }}</span>
		</button>
	</div>
</template>

<script setup>
defineProps({
	modelValue: {
		type: String,
		default: "auto",
		validator: (v) => ["auto", "dev", "plan", "insights"].includes(v),
	},
});

defineEmits(["update:modelValue"]);

// Each mode has an icon (inline SVG path data), a label, a tooltip,
// and a color family. The icon + color combination gives the four
// modes distinct visual identities without relying on label text
// alone - useful because the modes map to very different pipeline
// behaviours:
//   - Auto     (green, sparkles)    : orchestrator decides
//   - Dev      (blue, wrench)       : build + deploy via the crew
//   - Plan     (indigo, clipboard)  : produce a reviewable plan doc
//   - Insights (cyan, magnifier)    : read-only Q&A about the site
//
// SVG path data is kept tight (single-path where possible) because
// Vue has to parse and re-render it on every mode change.
const options = [
	{
		value: "auto",
		label: "Auto",
		// Sparkles / magic - small 4-point stars
		svg: '<path d="M12 3l1.5 4.5L18 9l-4.5 1.5L12 15l-1.5-4.5L6 9l4.5-1.5z"/><path d="M19 14l0.75 2.25L22 17l-2.25 0.75L19 20l-0.75-2.25L16 17l2.25-0.75z"/><path d="M5 16l0.5 1.5L7 18l-1.5 0.5L5 20l-0.5-1.5L3 18l1.5-0.5z"/>',
		tooltip: "Auto - Alfred picks the right mode based on your prompt",
	},
	{
		value: "dev",
		label: "Dev",
		// Code brackets: </>
		svg: '<polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/>',
		tooltip: "Dev - Build and deploy via the full SDLC crew",
	},
	{
		value: "plan",
		label: "Plan",
		// Clipboard with checklist
		svg: '<rect x="8" y="2" width="8" height="4" rx="1" ry="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><line x1="9" y1="12" x2="15" y2="12"/><line x1="9" y1="16" x2="15" y2="16"/>',
		tooltip: "Plan - Describe the approach without building anything",
	},
	{
		value: "insights",
		label: "Insights",
		// Magnifying glass
		svg: '<circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
		tooltip: "Insights - Read-only questions about your site state",
	},
];
</script>

<style scoped>
/* Container: segmented control with a visible border and a tinted
   background so the group reads as a single control, not four
   floating buttons. */
.alfred-mode-switcher {
	display: inline-flex;
	gap: 2px;
	padding: 3px;
	background: var(--bg-tertiary, #f3f4f6);
	border-radius: 8px;
	border: 1px solid var(--border-color, #e5e7eb);
	box-shadow: inset 0 1px 2px rgba(0, 0, 0, 0.03);
}

/* Individual mode button: icon + label, tight padding, smooth
   transitions. Default state is muted text on a transparent
   background so the active state pops. */
.alfred-mode-btn {
	display: inline-flex;
	align-items: center;
	gap: 6px;
	padding: 5px 11px;
	font-size: 12px;
	font-weight: 500;
	color: var(--text-muted, #6b7280);
	background: transparent;
	border: 1px solid transparent;
	border-radius: 5px;
	cursor: pointer;
	transition:
		background 0.14s ease,
		color 0.14s ease,
		border-color 0.14s ease,
		box-shadow 0.14s ease,
		transform 0.05s ease;
}

.alfred-mode-icon {
	width: 12px;
	height: 12px;
	flex-shrink: 0;
	opacity: 0.75;
	transition: opacity 0.14s ease, transform 0.14s ease;
}

.alfred-mode-label {
	line-height: 1;
}

.alfred-mode-btn:hover {
	color: var(--text-color, #111827);
	background: var(--bg-secondary, #ffffff);
}

.alfred-mode-btn:hover .alfred-mode-icon {
	opacity: 1;
}

.alfred-mode-btn:active {
	transform: translateY(1px);
}

/* Active state (neutral base). Per-mode colour overrides below
   give each mode its own identity. */
.alfred-mode-btn-active {
	color: var(--text-color, #111827);
	background: var(--bg-color, #ffffff);
	border-color: var(--border-color, #e5e7eb);
	box-shadow:
		0 1px 2px rgba(0, 0, 0, 0.06),
		0 1px 1px rgba(0, 0, 0, 0.04);
}

.alfred-mode-btn-active .alfred-mode-icon {
	opacity: 1;
}

/* ── Per-mode colour schemes ───────────────────────────────────
   Each active mode gets its own tinted background, text color,
   and border accent so the user can tell at a glance which mode
   is selected, even peripherally. Tints are chosen to be visually
   distinct but not jarring. */

/* Auto - green (the "smart default" mode) */
.alfred-mode-btn-active.alfred-mode-btn-auto {
	color: #047857;
	background: #ecfdf5;
	border-color: #a7f3d0;
	box-shadow:
		0 1px 2px rgba(4, 120, 87, 0.12),
		inset 0 0 0 1px rgba(167, 243, 208, 0.4);
}
.alfred-mode-btn-auto:hover:not(.alfred-mode-btn-active) {
	color: #047857;
}

/* Dev - blue (the "build" mode - most frequent) */
.alfred-mode-btn-active.alfred-mode-btn-dev {
	color: #1d4ed8;
	background: #eff6ff;
	border-color: #bfdbfe;
	box-shadow:
		0 1px 2px rgba(29, 78, 216, 0.12),
		inset 0 0 0 1px rgba(191, 219, 254, 0.4);
}
.alfred-mode-btn-dev:hover:not(.alfred-mode-btn-active) {
	color: #1d4ed8;
}

/* Plan - indigo (the "design before build" mode) */
.alfred-mode-btn-active.alfred-mode-btn-plan {
	color: #4338ca;
	background: #eef2ff;
	border-color: #c7d2fe;
	box-shadow:
		0 1px 2px rgba(67, 56, 202, 0.12),
		inset 0 0 0 1px rgba(199, 210, 254, 0.4);
}
.alfred-mode-btn-plan:hover:not(.alfred-mode-btn-active) {
	color: #4338ca;
}

/* Insights - cyan (the "read-only explore" mode) */
.alfred-mode-btn-active.alfred-mode-btn-insights {
	color: #0891b2;
	background: #ecfeff;
	border-color: #a5f3fc;
	box-shadow:
		0 1px 2px rgba(8, 145, 178, 0.12),
		inset 0 0 0 1px rgba(165, 243, 252, 0.4);
}
.alfred-mode-btn-insights:hover:not(.alfred-mode-btn-active) {
	color: #0891b2;
}
</style>

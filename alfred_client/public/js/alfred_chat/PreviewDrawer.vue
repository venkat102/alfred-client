<template>
	<!-- Scrim: only renders on mobile (media query on the scrim class
	     itself controls visibility). Click dismisses the drawer. -->
	<div
		v-if="modelValue"
		class="alfred-drawer-scrim alfred-drawer-scrim--open"
		@click="$emit('update:modelValue', false)"
		aria-hidden="true"
	></div>

	<aside
		:class="[
			'alfred-drawer',
			{
				'alfred-drawer--open': modelValue,
				'alfred-drawer--no-anim': !ready,
			},
		]"
		:role="dialogRole"
		:aria-hidden="!modelValue"
		aria-labelledby="alfred-drawer-title"
		ref="rootEl"
	>
		<header class="alfred-drawer-head">
			<div class="alfred-drawer-head-left">
				<div class="alfred-mark alfred-mark--preview alfred-mark--sm" aria-hidden="true">&#9670;</div>
				<h2 id="alfred-drawer-title" class="alfred-drawer-title">{{ __("Preview") }}</h2>
				<span v-if="changeCount" class="alfred-chip alfred-chip--neutral alfred-drawer-count">{{ changeCount }}</span>
			</div>
			<div class="alfred-drawer-head-right">
				<button
					type="button"
					class="alfred-icon-btn"
					@click="$emit('minimize')"
					:aria-label="__('Minimize preview')"
					:title="__('Minimize')"
					ref="minimizeBtnEl"
				>
					<span class="alfred-btn-glyph" aria-hidden="true">&minus;</span>
				</button>
				<button
					type="button"
					class="alfred-icon-btn"
					@click="$emit('update:modelValue', false)"
					:aria-label="__('Close preview')"
					:title="__('Close')"
					ref="closeBtnEl"
				>
					<span class="alfred-btn-glyph" aria-hidden="true">&#10005;</span>
				</button>
			</div>
		</header>
		<div class="alfred-drawer-body">
			<PreviewPanel
				:changeset="changeset"
				:current-phase="currentPhase"
				:deploy-steps="deploySteps"
				:deployed="deployed"
				:is-processing="isProcessing"
				:conversation-status="conversationStatus"
				:validating="validating"
				:rollback-in-flight="rollbackInFlight"
				@approve="$emit('approve')"
				@modify="$emit('modify')"
				@reject="$emit('reject')"
				@rollback="$emit('rollback')"
			/>
		</div>
	</aside>
</template>

<script setup>
import { computed, nextTick, ref, watch } from "vue";
import PreviewPanel from "./PreviewPanel.vue";

const props = defineProps({
	modelValue: { type: Boolean, default: false },
	// PreviewPanel pass-throughs
	changeset: { type: Object, default: null },
	currentPhase: { type: String, default: null },
	deploySteps: { type: Array, default: () => [] },
	deployed: { type: Boolean, default: false },
	isProcessing: { type: Boolean, default: false },
	conversationStatus: { type: String, default: "" },
	validating: { type: Boolean, default: false },
	rollbackInFlight: { type: Boolean, default: false },
});

const emit = defineEmits([
	"update:modelValue",
	"minimize",
	"approve",
	"modify",
	"reject",
	"rollback",
]);

const rootEl = ref(null);
const closeBtnEl = ref(null);
const minimizeBtnEl = ref(null);

// ready gates the slide animation on first paint: when the drawer is
// mounted already open (from localStorage), we want to skip the
// translateX transition so it does not flash in. We flip ready true on
// the next tick so subsequent toggles do animate.
const ready = ref(false);
nextTick(() => { ready.value = true; });

// Use dialog role only on viewports where the drawer is modal
// (mobile). On desktop it is a non-modal complementary panel so
// role=complementary reads more accurately for screen readers.
const isMobile = computed(() => {
	if (typeof window === "undefined") return false;
	return window.matchMedia && window.matchMedia("(max-width: 768px)").matches;
});
const dialogRole = computed(() => (isMobile.value ? "dialog" : "complementary"));

const changeCount = computed(() => {
	const raw = props.changeset?.changes;
	if (!raw) return 0;
	try {
		const arr = typeof raw === "string" ? JSON.parse(raw) : raw;
		return Array.isArray(arr) ? arr.length : 0;
	} catch { return 0; }
});

// Focus the close button when the drawer opens on mobile so Escape
// and tab navigation feel natural. Desktop keeps whatever had focus
// (e.g. the composer) because the drawer does not steal interaction.
watch(() => props.modelValue, (open) => {
	if (!open) return;
	nextTick(() => {
		if (isMobile.value) {
			closeBtnEl.value?.focus();
		}
	});
});
</script>

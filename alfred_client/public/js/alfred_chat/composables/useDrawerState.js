/**
 * Preview drawer open/close state, persisted to localStorage.
 *
 * Owns:
 *   - drawerOpen: ref(boolean) - is the right-hand preview drawer visible
 *   - unseenChanges: ref(boolean) - true when a changeset landed while the
 *     drawer was closed; lights up the toolbar dot and the minimized-pill
 *     badge until the user opens the drawer
 *   - setDrawerOpen / openDrawer / closeDrawer / toggleDrawer / minimizeDrawer
 *     - four-way helpers so callers never mutate drawerOpen directly
 *
 * Side effects:
 *   - writes the current state to window.localStorage on every change
 *   - toggles `alfred-drawer-open` on document.body so page-level CSS can
 *     shove the chat area aside on desktop
 *
 * Callers that mount/unmount the chat app are responsible for applying or
 * clearing the body class at their lifecycle boundaries (onMounted /
 * onUnmounted) so stale DOM state doesn't survive a hot reload.
 */

import { ref } from "vue";

const DRAWER_LS_KEY = "alfred_chat_drawer_open";

function _readDrawerOpenFromStorage() {
	try {
		return window.localStorage.getItem(DRAWER_LS_KEY) === "true";
	} catch {
		return false;
	}
}

function _writeDrawerOpenToStorage(value) {
	try {
		window.localStorage.setItem(DRAWER_LS_KEY, value ? "true" : "false");
	} catch {
		/* storage disabled; in-memory state still works */
	}
}

export function useDrawerState() {
	const drawerOpen = ref(_readDrawerOpenFromStorage());
	const unseenChanges = ref(false);

	function setDrawerOpen(value) {
		drawerOpen.value = !!value;
		if (value) unseenChanges.value = false;
		_writeDrawerOpenToStorage(drawerOpen.value);
		document.body.classList.toggle("alfred-drawer-open", drawerOpen.value);
	}
	function openDrawer() { setDrawerOpen(true); }
	function closeDrawer() { setDrawerOpen(false); }
	function toggleDrawer() { setDrawerOpen(!drawerOpen.value); }
	function minimizeDrawer() { setDrawerOpen(false); }

	return {
		drawerOpen,
		unseenChanges,
		setDrawerOpen,
		openDrawer,
		closeDrawer,
		toggleDrawer,
		minimizeDrawer,
	};
}

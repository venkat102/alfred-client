/**
 * Alfred Client — Frappe bundle entry point
 *
 * This file is automatically built by `bench build --app alfred_client`.
 * It exports Vue components for the Alfred chat page.
 *
 * Note: In Frappe v15+, Vue 3 is available globally as `window.Vue`.
 * SFC components need a build step (vite/rollup) to compile .vue files.
 * Until that's configured, the page falls back to the CSS-only version.
 */

// Export components for the page shell to mount
import AlfredChat from "./alfred/AlfredChat.vue";

export { AlfredChat };

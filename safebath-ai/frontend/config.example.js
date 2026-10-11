/*
 * SafeBath AI — optional local API-base configuration (committed EXAMPLE).
 *
 * The dashboard is normally served from the laptop (`python -m http.server`
 * or `npm run serve` in frontend/); in that case the browser's page host is
 * "localhost", so the shared apiBase.mjs resolver cannot know the Raspberry
 * Pi's address and would fall back to http://localhost:8000 (works only when
 * the API runs on THIS machine too).
 *
 * To use a different edge server on the LAN, copy this file to `config.local.js`
 * and set the base:
 *
 *     copy config.example.js config.local.js
 *     # then edit config.local.js:
 *     window.SAFEBATH_API_BASE = "http://192.168.43.66:8000";
 *
 * config.local.js is git-ignored and only read on THIS machine: a per-machine
 * override, never the shipped default. It is loaded by index.html and
 * caretaker.html (before the bundle), so no rebuild is needed — just refresh.
 *
 * Hosted (https) pages MUST NOT point at a private/plain-http edge server:
 * browsers block mixed content. There, set SAFEBATH_API_BASE to an https
 * proxy/cloud backend or rely on the existing Supabase cloud sync.
 */
window.SAFEBATH_API_BASE = null;
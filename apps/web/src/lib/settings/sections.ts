/**
 * Anchors on the Settings screen that something else links to — 2026-10-01.
 *
 * Kept apart from `SettingsView` so the scan-limit notice can build its link
 * without importing a whole screen, and so the id and the link cannot drift
 * apart: both come from here.
 */

/** The id on the Billing section, where Manage billing is. */
export const BILLING_SECTION_ID = 'billing';

/** Where to send somebody who has to deal with an existing subscription. */
export const BILLING_SETTINGS_HREF = `/settings#${BILLING_SECTION_ID}`;

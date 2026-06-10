export const ACTIVE_STATES = new Set(['open', 'acknowledged']);
export const DEFAULT_LIFECYCLE = { state: 'open', since: null, snoozeUntil: null, note: null };

/**
 * Split findings into active vs suppressed based on persisted lifecycle states.
 * Snoozed findings whose snoozeUntil has passed are reactivated (open). Pure.
 * @param {object[]} findings  findings with a `.key`
 * @param {Record<string, object>} states  key -> { state, since, snoozeUntil, note }
 * @param {number} nowMs  current time in ms (for snooze expiry); 0 disables expiry
 * @returns {{ active: object[], suppressed: object[] }}
 */
export function applyLifecycle(findings, states = {}, nowMs = 0) {
  const active = [];
  const suppressed = [];
  for (const f of findings) {
    const raw = (f.key && states[f.key]) ? states[f.key] : DEFAULT_LIFECYCLE;
    let lc = { ...DEFAULT_LIFECYCLE, ...raw };
    if (nowMs > 0 && lc.state === 'snoozed' && lc.snoozeUntil != null && Date.parse(lc.snoozeUntil) < nowMs) {
      lc = { ...lc, state: 'open', snoozeUntil: null };
    }
    const annotated = { ...f, lifecycle: lc };
    (ACTIVE_STATES.has(lc.state) ? active : suppressed).push(annotated);
  }
  return { active, suppressed };
}

/**
 * Pure state transition — returns a NEW states map (does not mutate input).
 * @param {Record<string,object>} states
 * @param {string} key
 * @param {'open'|'acknowledged'|'snoozed'|'resolved'|'wontfix'} state
 * @param {{ snoozeUntil?:string, note?:string, now?:string }} [opts]
 */
export function setState(states = {}, key, state, opts = {}) {
  return {
    ...states,
    [key]: {
      state,
      since: opts.now ?? null,
      snoozeUntil: opts.snoozeUntil ?? null,
      note: opts.note ?? null,
    },
  };
}

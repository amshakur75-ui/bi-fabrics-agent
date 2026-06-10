import { DEFAULT_CONFIG } from './config.js';

/**
 * Should a scheduled audit fire now? Pure (time is injected via `now`).
 * @param {{ cadence?:'hourly'|'daily'|'weekly', atHour?:number, atMinute?:number, dayOfWeek?:number }} schedule
 * @param {{ hour:number, minute:number, dayOfWeek:number }} now  dayOfWeek 0=Sun..6=Sat
 * @returns {boolean}
 */
export function shouldRunScheduled(schedule = {}, now = {}) {
  const { cadence = 'daily', atHour = 6, atMinute = 0, dayOfWeek: targetDayOfWeek = 1 } = schedule;
  if (now.minute !== atMinute) return false;
  if (cadence === 'hourly') return true;
  if (cadence === 'daily') return now.hour === atHour;
  if (cadence === 'weekly') return now.hour === atHour && now.dayOfWeek === targetDayOfWeek;
  return false;
}

/**
 * Immediate threshold-trigger events from the current facts. Pure.
 * @param {object} facts
 * @param {object} [config]
 * @returns {Array<{ reason:string, severity:string }>}
 */
export function evaluateThresholdTriggers(facts = {}, config = DEFAULT_CONFIG) {
  const events = [];
  const c = facts?.capacity;
  if (c && (c.peakCuPct ?? 0) >= config.capacity.throttleCritPct) {
    events.push({ reason: `Capacity ${c.capacityId} at ${c.peakCuPct}% CU (>= ${config.capacity.throttleCritPct}% critical)`, severity: 'Critical' });
  }
  for (const p of facts?.pipelines ?? []) {
    if (p.lastStatus === 'Failed') events.push({ reason: `Pipeline "${p.name}" failed`, severity: 'Critical' });
  }
  for (const g of facts?.access?.adminGrants ?? []) {
    if (/admin/i.test(g.role) && g.sensitive) {
      events.push({ reason: `Admin grant on sensitive workspace "${g.workspace}"`, severity: 'Critical' });
    }
  }
  return events;
}

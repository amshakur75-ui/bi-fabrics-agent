import { domainOf } from './key-utils.js';

export const DEFAULT_ROUTES = {
  security: 'security-team',
  cost: 'finops',
  capacity: 'powerbi-team',
  model: 'powerbi-team',
  report: 'powerbi-team',
  pipeline: 'powerbi-team',
  lineage: 'powerbi-team',
  meta: 'powerbi-team',
};

/**
 * Group finding keys by destination owner based on their domain. Pure.
 * @param {object[]} findings  findings with .key
 * @param {Record<string,string>} [routes]  domain -> destination
 * @returns {Record<string, string[]>}  destination -> finding keys
 */
export function routeFindings(findings = [], routes = DEFAULT_ROUTES) {
  const routed = {};
  for (const f of findings) {
    const dest = routes[domainOf(f.key)] ?? 'unrouted';
    (routed[dest] ??= []).push(f.key);
  }
  return routed;
}

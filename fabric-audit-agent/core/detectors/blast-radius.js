/** @typedef {import('./capacity.js').Flag} Flag */

function buildAdjacency(edges = []) {
  const downstream = new Map();
  const upstream = new Map();
  for (const e of edges) {
    if (!downstream.has(e.from)) downstream.set(e.from, []);
    downstream.get(e.from).push(e.to);
    if (!upstream.has(e.to)) upstream.set(e.to, []);
    upstream.get(e.to).push(e.from);
  }
  return { downstream, upstream };
}

function reachDownstream(startId, downstream) {
  const seen = new Set();
  const queue = [...(downstream.get(startId) ?? [])];
  while (queue.length) {
    const id = queue.shift();
    if (seen.has(id) || id === startId) continue;   // exclude root + cycle-safe
    seen.add(id);
    for (const next of downstream.get(id) ?? []) if (!seen.has(next)) queue.push(next);
  }
  return [...seen];
}

/**
 * For each root-cause failure (a Failed node with no Failed upstream), emit one finding
 * listing every downstream asset reachable from it. Pure: facts in, flags out.
 * @param {{lineage?:{nodes:object[], edges:object[]}}} facts
 * @param {object} [_config] accepted for API uniformity; no tunable thresholds here
 * @returns {Flag[]}
 */
export function detectBlastRadius(facts, _config) {
  const lineage = facts?.lineage;
  if (!lineage?.nodes?.length) return [];

  const nodeById = new Map(lineage.nodes.map(n => [n.id, n]));
  const { downstream, upstream } = buildAdjacency(lineage.edges ?? []);
  const isFailed = (id) => nodeById.get(id)?.status === 'Failed';

  const rootCauses = lineage.nodes.filter(
    n => n.status === 'Failed' && !(upstream.get(n.id) ?? []).some(isFailed),
  );

  return rootCauses.map(rc => {
    const affected = reachDownstream(rc.id, downstream).map(id => nodeById.get(id)?.name ?? id);
    return {
      type: 'lineage.blast-radius',
      resource: `${rc.workspace} / ${rc.name}`,
      when: rc.failedAt ?? '',
      evidence: { root: rc.name, rootType: rc.type, affected, affectedCount: affected.length },
      what: `${rc.type} "${rc.name}" failed, impacting ${affected.length} downstream asset(s)${affected.length ? ': ' + affected.join(', ') : ''}.`,
    };
  });
}

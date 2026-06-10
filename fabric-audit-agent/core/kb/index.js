import { CAPACITY_PLAYBOOKS } from './capacity.js';
import { MODEL_PLAYBOOKS } from './model.js';
import { REPORT_PLAYBOOKS } from './report.js';
import { PIPELINE_PLAYBOOKS } from './pipeline.js';
import { LINEAGE_PLAYBOOKS } from './lineage.js';
import { SECURITY_PLAYBOOKS } from './security.js';
import { COST_PLAYBOOKS } from './cost.js';
import { META_PLAYBOOKS } from './meta.js';

const ALL = { ...CAPACITY_PLAYBOOKS, ...MODEL_PLAYBOOKS, ...REPORT_PLAYBOOKS, ...PIPELINE_PLAYBOOKS, ...LINEAGE_PLAYBOOKS, ...SECURITY_PLAYBOOKS, ...COST_PLAYBOOKS, ...META_PLAYBOOKS };

const DEFAULT = {
  rootCause: 'Pattern not yet in the knowledge base.',
  fixes: ['Investigate manually and add a playbook entry.'],
  owner: 'Power BI team',
};

/** @param {string} flagType @returns {{rootCause:string, fixes:string[], owner:string}} */
export function getRemediation(flagType) {
  return ALL[flagType] ?? DEFAULT;
}

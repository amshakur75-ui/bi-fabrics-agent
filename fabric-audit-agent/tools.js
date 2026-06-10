import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { createMockCollector } from './adapters/collector.mock.js';
import { createStubReasoner } from './adapters/reasoner.stub.js';
import { createFileDelivery } from './adapters/delivery.file.js';
import { createLocalStore } from './adapters/store.local.js';
import { runAudit } from './core/pipeline.js';

const __dirname = dirname(fileURLToPath(import.meta.url));

/**
 * Tool definitions in BaseAgent/Anthropic format. Each tool may carry a `_handler(input)`
 * that BaseAgent invokes. The audit is READ-ONLY — the handler only reads (mock) telemetry
 * and writes findings to local files; it never mutates any estate.
 * @returns {object[]}
 */
export function createToolDefinitions() {
  return [
    {
      name: 'run_audit',
      description: 'Run a read-only Fabric/Power BI audit over the current estate and return prioritized findings, a digest, and the capacity verdict (optimize vs size-up). Read-only: never modifies anything.',
      input_schema: { type: 'object', properties: {}, required: [] },
      _handler: async (_input) => {
        const collector = createMockCollector(join(__dirname, 'fixtures', 'estate.json'));
        const reasoner = createStubReasoner();
        const store = createLocalStore(join(__dirname, 'runs', 'history.json'));
        const delivery = createFileDelivery(join(__dirname, 'runs', 'latest.json'));
        const envelope = await runAudit({ collector, reasoner, delivery, store, agentId: 'fabric-audit-agent' });
        return {
          summary: envelope.summary,
          verdict: envelope.data.verdict,
          digest: envelope.data.digest,
          findings: envelope.data.findings,
        };
      },
    },
  ];
}

export default createToolDefinitions;

// Local validation runner — feed YOUR real numbers (my-estate.json) through the detectors
// and print the diagnosis. 100% local: no network, no API key, nothing leaves this machine.
// Run from this folder:  node mytest.js
import { readFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { detectAll } from './core/detectors/index.js';
import { createStubReasoner } from './adapters/reasoner.stub.js';
import { buildHealthScore } from './core/health-score.js';
import { buildRoadmap } from './core/roadmap.js';
import { buildCapacityVerdict } from './core/verdict.js';

const __dirname = dirname(fileURLToPath(import.meta.url));

const target = join(__dirname, 'my-estate.json');
if (!existsSync(target)) {
  console.log('\n  my-estate.json not found yet.');
  console.log('  Copy the template first (your copy stays private — it is gitignored):');
  console.log('     terminal:  cp my-estate.example.json my-estate.json');
  console.log('     VS Code:   right-click my-estate.example.json -> Copy, then Paste & rename to my-estate.json');
  console.log('  Then fill in your real numbers and re-run:  node mytest.js\n');
  process.exit(0);
}

const facts = JSON.parse(await readFile(target, 'utf-8'));
const flags = detectAll(facts);
const findings = await createStubReasoner().reason(facts, flags);

console.log(`\n================  YOUR ESTATE — DIAGNOSIS  ================\n`);
if (!findings.length) {
  console.log('No issues detected from the numbers you entered.');
  console.log('(If you expected findings, double-check the values in my-estate.json.)');
} else {
  for (const f of findings) {
    console.log(`[${f.score.level}] ${f.what}`);
    console.log(`    Why:    ${f.why}`);
    console.log(`    Impact: ${f.impact}`);
    console.log(`    Fix:    ${(f.fix ?? [])[0] ?? ''}`);
    console.log('');
  }
  const health = buildHealthScore(findings);
  const verdict = buildCapacityVerdict(facts, flags);
  const roadmap = buildRoadmap(findings);
  console.log(`-----------------------------------------------------------`);
  console.log(`Findings: ${findings.length}   Health: ${health.overall}/100`);
  console.log(`By domain: ${JSON.stringify(health.byDomain)}`);
  console.log(`Capacity verdict: ${verdict.decision.toUpperCase()} — ${verdict.reason}`);
  console.log(`\nDo these first:`);
  roadmap.slice(0, 5).forEach(r => console.log(`   #${r.rank} [${r.level}] ${r.what}`));
}
console.log(`\n===========================================================\n`);

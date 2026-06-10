import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, join } from 'node:path';
import { readFile } from 'node:fs/promises';
import { detectAll } from './core/detectors/index.js';
import { createStubReasoner } from './adapters/reasoner.stub.js';
import { scoreCase, scoreSuite } from './core/eval.js';

const __dirname = dirname(fileURLToPath(import.meta.url));

/** Run the golden suite through detect→stub-reason and score it. */
export async function runEval(casesPath = join(__dirname, 'fixtures', 'golden', 'cases.json')) {
  const cases = JSON.parse(await readFile(casesPath, 'utf-8'));
  const reasoner = createStubReasoner();
  const results = [];
  for (const c of cases) {
    const findings = await reasoner.reason(c.facts, detectAll(c.facts));
    results.push({ name: c.name, score: scoreCase(findings, c.expected) });
  }
  return { results, suite: scoreSuite(results) };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const { results, suite } = await runEval();
  for (const r of results) console.log(`${r.score.pass ? 'PASS' : 'FAIL'} ${r.name} (recall ${r.score.recall}, precision ${r.score.precision})${r.score.missing.length ? ' missing: ' + r.score.missing.join(',') : ''}`);
  console.log(`Suite: ${suite.passed}/${suite.cases} passed, avgRecall ${suite.avgRecall}, avgPrecision ${suite.avgPrecision}`);
  process.exitCode = suite.failed > 0 ? 1 : 0;
}

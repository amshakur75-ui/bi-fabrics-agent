// Local validation runner — feed YOUR real numbers (my-estate.json) through the detectors
// and print the diagnosis. 100% local: no network, no API key, nothing leaves this machine.
//
//   node mytest.js
//
// Don't have my-estate.json yet? Either:
//   - import it from your export:  node import.js yourfile.csv
//   - or copy the template:        cp my-estate.example.json my-estate.json  (then fill it in)
import { readFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { diagnose, formatDiagnosis } from './core/diagnosis.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const target = join(__dirname, 'my-estate.json');

if (!existsSync(target)) {
  console.log('\n  my-estate.json not found yet. Two ways to create it:');
  console.log('     import your export:  node import.js yourfile.csv   (or .vpax)');
  console.log('     copy the template:   cp my-estate.example.json my-estate.json   (then fill it in)');
  console.log('  Then re-run:  node mytest.js\n');
  process.exit(0);
}

const facts = JSON.parse(await readFile(target, 'utf-8'));
console.log(formatDiagnosis(await diagnose(facts)));

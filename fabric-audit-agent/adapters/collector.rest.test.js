import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { createRestCollector, fetchAllPages } from './collector.rest.js';
import { detectAll } from '../core/detectors/index.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const fixturePath = join(__dirname, '..', 'fixtures', 'raw', 'capacity-raw.json');

// Load the raw fixture once and build a fake http client that returns the
// capacity/refreshes envelopes by URL.
const rawFixture = JSON.parse(await readFile(fixturePath, 'utf-8'));

const CAPACITY_URL = 'https://api.example.com/capacities';
const REFRESHES_URL = 'https://api.example.com/refreshes';

function makeFakeHttp() {
  return {
    async getJson(url) {
      if (url === CAPACITY_URL) return rawFixture.capacity;
      if (url === REFRESHES_URL) return rawFixture.refreshes;
      throw new Error(`Unexpected URL: ${url}`);
    },
  };
}

const collector = createRestCollector({
  http: makeFakeHttp(),
  config: { capacityUrl: CAPACITY_URL, refreshesUrl: REFRESHES_URL },
});

test('REST collector assembles capacityId from displayName', async () => {
  const facts = await collector.collect();
  assert.equal(facts.capacity.capacityId, 'F64');
});

test('REST collector returns 3 refreshes from the fixture', async () => {
  const facts = await collector.collect();
  assert.equal(facts.capacity.refreshes.length, 3);
});

test('REST collector maps first refresh: durationMin === 47', async () => {
  const facts = await collector.collect();
  assert.equal(facts.capacity.refreshes[0].durationMin, 47);
});

test('REST collector maps first refresh: sizeGB === 4.2', async () => {
  const facts = await collector.collect();
  assert.equal(facts.capacity.refreshes[0].sizeGB, 4.2);
});

// End-to-end proof: real-shaped REST facts flow through the unchanged detectors
test('detectAll on REST-collected facts yields capacity.throttle and capacity.contention flags', async () => {
  const facts = await collector.collect();
  const flags = detectAll(facts);
  assert.ok(
    flags.some(f => f.type === 'capacity.throttle'),
    'expected capacity.throttle flag',
  );
  assert.ok(
    flags.some(f => f.type === 'capacity.contention'),
    'expected capacity.contention flag',
  );
});

// --- fetchAllPages ---

test('fetchAllPages follows two pages and concatenates .value arrays', async () => {
  const PAGE1_URL = 'https://api.example.com/items';
  const PAGE2_URL = 'https://api.example.com/items?page=2';
  const fakeHttp = {
    async getJson(url) {
      if (url === PAGE1_URL) return { value: [{ id: 1 }, { id: 2 }], nextLink: PAGE2_URL };
      if (url === PAGE2_URL) return { value: [{ id: 3 }] };
      throw new Error(`Unexpected URL: ${url}`);
    },
  };
  const result = await fetchAllPages(fakeHttp, PAGE1_URL);
  assert.equal(result.length, 3);
  assert.equal(result[0].id, 1);
  assert.equal(result[2].id, 3);
});

test('fetchAllPages returns single page items when no nextLink', async () => {
  const fakeHttp = {
    async getJson(_url) { return { value: [{ id: 'a' }] }; },
  };
  const result = await fetchAllPages(fakeHttp, 'https://api.example.com/x');
  assert.equal(result.length, 1);
});

test('fetchAllPages wraps non-array page in array', async () => {
  const fakeHttp = {
    async getJson(_url) { return { id: 'single', name: 'X' }; },
  };
  const result = await fetchAllPages(fakeHttp, 'https://api.example.com/single');
  assert.equal(result.length, 1);
  assert.equal(result[0].id, 'single');
});

// --- Full multi-domain end-to-end test ---

const estateRawPath = join(__dirname, '..', 'fixtures', 'raw', 'estate-raw.json');
const estateRaw = JSON.parse(await readFile(estateRawPath, 'utf-8'));

const ESTATE_CAPACITY_URL  = 'https://api.example.com/estate/capacities';
const ESTATE_REFRESHES_URL = 'https://api.example.com/estate/refreshes';
const ESTATE_DATASETS_URL  = 'https://api.example.com/estate/datasets';
const ESTATE_REPORTS_URL   = 'https://api.example.com/estate/reports';
const ESTATE_PIPELINES_URL = 'https://api.example.com/estate/pipelines';
const ESTATE_LINEAGE_URL   = 'https://api.example.com/estate/lineage';
const ESTATE_ACCESS_URL    = 'https://api.example.com/estate/access';
const ESTATE_USAGE_URL     = 'https://api.example.com/estate/usage';

function makeEstateHttp() {
  return {
    async getJson(url) {
      if (url === ESTATE_CAPACITY_URL)  return estateRaw.capacity;
      if (url === ESTATE_REFRESHES_URL) return estateRaw.refreshes;
      if (url === ESTATE_DATASETS_URL)  return estateRaw.datasets;
      if (url === ESTATE_REPORTS_URL)   return estateRaw.reports;
      if (url === ESTATE_PIPELINES_URL) return estateRaw.pipelines;
      if (url === ESTATE_LINEAGE_URL)   return estateRaw.lineage;
      if (url === ESTATE_ACCESS_URL)    return estateRaw.access;
      if (url === ESTATE_USAGE_URL)     return estateRaw.usage;
      throw new Error(`Unexpected URL: ${url}`);
    },
  };
}

const estateCollector = createRestCollector({
  http: makeEstateHttp(),
  config: {
    capacityUrl:  ESTATE_CAPACITY_URL,
    refreshesUrl: ESTATE_REFRESHES_URL,
    datasetsUrl:  ESTATE_DATASETS_URL,
    reportsUrl:   ESTATE_REPORTS_URL,
    pipelinesUrl: ESTATE_PIPELINES_URL,
    lineageUrl:   ESTATE_LINEAGE_URL,
    accessUrl:    ESTATE_ACCESS_URL,
    usageUrl:     ESTATE_USAGE_URL,
  },
});

test('full estate: collect() assembles models from raw datasets', async () => {
  const facts = await estateCollector.collect();
  assert.equal(facts.models.length, 2);
  assert.equal(facts.models[0].name, 'Sales');
  assert.equal(facts.models[0].sizeGB, 4.2);
});

test('full estate: collect() assembles reports from raw reports', async () => {
  const facts = await estateCollector.collect();
  assert.equal(facts.reports.length, 2);
  assert.equal(facts.reports[0].mode, 'DirectQuery');
});

test('full estate: collect() assembles pipelines from raw pipelines', async () => {
  const facts = await estateCollector.collect();
  assert.equal(facts.pipelines.length, 2);
  assert.equal(facts.pipelines[0].lastStatus, 'Failed');
});

test('full estate: collect() assembles lineage nodes and edges', async () => {
  const facts = await estateCollector.collect();
  assert.equal(facts.lineage.nodes.length, 3);
  assert.equal(facts.lineage.edges.length, 2);
});

test('full estate: collect() assembles access grants', async () => {
  const facts = await estateCollector.collect();
  assert.equal(facts.access.adminGrants.length, 1);
  assert.equal(facts.access.externalShares.length, 1);
});

test('full estate: collect() assembles usage data', async () => {
  const facts = await estateCollector.collect();
  assert.equal(facts.usage.reports.length, 2);
  assert.equal(facts.usage.capacities.length, 1);
});

test('detectAll on full estate facts produces flags spanning all expected domains', async () => {
  const facts = await estateCollector.collect();
  const flags = detectAll(facts);
  const types = flags.map(f => f.type);
  const prefixes = ['capacity.', 'model.', 'report.', 'pipeline.', 'lineage.', 'security.', 'cost.'];
  for (const prefix of prefixes) {
    assert.ok(
      types.some(t => t.startsWith(prefix)),
      `expected at least one flag with prefix "${prefix}", got: ${types.join(', ')}`,
    );
  }
});

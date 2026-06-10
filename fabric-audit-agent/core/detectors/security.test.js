import { test } from 'node:test';
import assert from 'node:assert/strict';
import { detectSecurity } from './security.js';

test('sensitive admin grant produces security.admin-grant flag', () => {
  const facts = {
    access: {
      adminGrants: [
        { workspace: 'Finance', principal: 'ext-contractor@vendor.com', role: 'Admin', grantedAt: '2026-06-07T22:10:00.000Z', sensitive: true },
      ],
    },
  };
  const flags = detectSecurity(facts);
  assert.equal(flags.length, 1);
  assert.equal(flags[0].type, 'security.admin-grant');
  assert.equal(flags[0].resource, 'Finance');
  assert.equal(flags[0].evidence.principal, 'ext-contractor@vendor.com');
});

test('non-sensitive admin grant produces no flag', () => {
  const facts = {
    access: {
      adminGrants: [
        { workspace: 'Finance', principal: 'internal@corp.com', role: 'Admin', grantedAt: '2026-06-07T22:10:00.000Z', sensitive: false },
      ],
    },
  };
  assert.deepEqual(detectSecurity(facts), []);
});

test('non-admin role (even sensitive) produces no flag', () => {
  const facts = {
    access: {
      adminGrants: [
        { workspace: 'Finance', principal: 'viewer@corp.com', role: 'Viewer', grantedAt: '2026-06-07T22:10:00.000Z', sensitive: true },
      ],
    },
  };
  assert.deepEqual(detectSecurity(facts), []);
});

test('external share produces security.external-share flag', () => {
  const facts = {
    access: {
      externalShares: [
        { workspace: 'Finance', item: 'Exec Dashboard', sharedWith: 'partner@othercorp.com', at: '2026-06-06T15:00:00.000Z' },
      ],
    },
  };
  const flags = detectSecurity(facts);
  assert.equal(flags.length, 1);
  assert.equal(flags[0].type, 'security.external-share');
  assert.equal(flags[0].resource, 'Finance / Exec Dashboard');
  assert.equal(flags[0].evidence.sharedWith, 'partner@othercorp.com');
});

test('accessEvents 220/20 produces security.unusual-access with ratio 11', () => {
  const facts = {
    access: {
      accessEvents: [
        { user: 'jdoe', workspace: 'Finance', count: 220, baselineCount: 20 },
      ],
    },
  };
  const flags = detectSecurity(facts);
  assert.equal(flags.length, 1);
  assert.equal(flags[0].type, 'security.unusual-access');
  assert.equal(flags[0].evidence.ratio, 11);
  assert.equal(flags[0].evidence.user, 'jdoe');
});

test('accessEvents with ratio < 5 produces no flag', () => {
  const facts = {
    access: {
      accessEvents: [
        { user: 'jdoe', workspace: 'Finance', count: 40, baselineCount: 20 },
      ],
    },
  };
  assert.deepEqual(detectSecurity(facts), []);
});

test('detectSecurity({}) returns []', () => {
  assert.deepEqual(detectSecurity({}), []);
});

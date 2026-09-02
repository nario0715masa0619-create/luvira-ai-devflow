const assert = require('assert/strict');
const { validateContextLock } = require('../scripts/validate-context-lock');

const now = Date.parse('2026-09-02T00:00:00Z');
const valid = {
  version: 1,
  projectId: 'example-product',
  repository: 'owner/example-product',
  issue: 123,
  approvalUrl: 'https://github.com/owner/example-product/issues/123#issuecomment-1',
  expiresAt: '2026-12-31T00:00:00Z',
  allowedActions: ['read', 'plan', 'comment'],
  protectedBranch: 'main'
};

function check(lock, action = 'read') {
  return validateContextLock(lock, { repository: 'owner/example-product', requestedAction: action, now });
}

assert.equal(check(valid), null);
assert.match(check({ ...valid, repository: 'owner/other-project' }), /repository/);
assert.match(check({ ...valid, issue: 124 }), /approvalUrl/);
assert.match(check({ ...valid, expiresAt: '2026-01-01T00:00:00Z' }), /expired/);
assert.match(check(valid, 'branch'), /not allowed/);
assert.match(check({ ...valid, allowedActions: [...valid.allowedActions, 'merge'] }, 'merge'), /prohibited/);

console.log('Context Lock validation tests passed.');

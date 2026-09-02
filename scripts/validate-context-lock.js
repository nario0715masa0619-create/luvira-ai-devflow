#!/usr/bin/env node

const fs = require('fs');

// This validator is intentionally dependency-free so every PR can test it.
const prohibitedActions = new Set(['merge', 'deploy', 'admin', 'secret']);

function validateContextLock(lock, { repository, requestedAction, now = Date.now() }) {
  const required = ['version', 'projectId', 'repository', 'issue', 'approvalUrl', 'expiresAt', 'allowedActions', 'protectedBranch'];
  for (const key of required) {
    if (!(key in lock)) return `missing field: ${key}`;
  }
  if (lock.version !== 1) return 'unsupported version';
  if (!/^[a-z0-9][a-z0-9-]{1,62}$/.test(lock.projectId)) return 'invalid projectId';
  if (lock.repository !== repository) return 'repository does not match caller';
  if (!Number.isInteger(lock.issue) || lock.issue < 1) return 'invalid issue';
  const approvalPrefix = `https://github.com/${lock.repository}/issues/${lock.issue}`;
  if (!lock.approvalUrl.startsWith(approvalPrefix)) return 'approvalUrl does not bind to target issue';
  if (!Array.isArray(lock.allowedActions) || !lock.allowedActions.includes(requestedAction)) return 'requested action is not allowed';
  if (prohibitedActions.has(requestedAction)) return 'requested action is prohibited by v1';
  const expiry = Date.parse(lock.expiresAt);
  if (Number.isNaN(expiry) || expiry <= now) return 'Context Lock is expired or invalid';
  return null;
}

function main() {
  const [lockPath, requestedAction, repository] = process.argv.slice(2);
  if (!lockPath || !requestedAction || !repository) {
    console.error('Usage: validate-context-lock.js <path> <requested-action> <repository>');
    process.exit(2);
  }
  let lock;
  try {
    lock = JSON.parse(fs.readFileSync(lockPath, 'utf8'));
  } catch {
    console.error('CONTEXT_LOCK_BLOCKED: missing or invalid JSON Context Lock');
    process.exit(1);
  }
  const error = validateContextLock(lock, { repository, requestedAction });
  if (error) {
    console.error(`CONTEXT_LOCK_BLOCKED: ${error}`);
    process.exit(1);
  }
  console.log(`CONTEXT_LOCK_PASS project=${lock.projectId} repository=${lock.repository} issue=${lock.issue} action=${requestedAction}`);
}

if (require.main === module) main();

module.exports = { validateContextLock };

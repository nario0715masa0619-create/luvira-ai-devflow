const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const workflow = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'workflows', 'orca-product-devflow-intake-v1.json'), 'utf8'));
const nodes = new Map(workflow.nodes.map(node => [node.name, node]));
for (const name of ['Webhook - Orca Product Intake', 'Validate Ready Product Approval Issue', 'IF Product Intake Is Valid', 'Create Control Approval Issue', 'Build Product Intake Result', 'Build Product Intake Rejection', 'Respond to Orca']) assert.ok(nodes.has(name), `missing node: ${name}`);
const validate = nodes.get('Validate Ready Product Approval Issue').parameters.jsCode;
assert.match(validate, /project-/);
assert.match(validate, /luvira-ai-devflow/);
assert.match(validate, /luvira-ai-devflow-staging/);
assert.match(validate, /ORCA_PRODUCT_REPOSITORY_INVALID/);
assert.match(validate, /valid: false/);
assert.doesNotMatch(validate, /throw new Error/);
assert.match(nodes.get('IF Product Intake Is Valid').parameters.conditions.conditions[0].leftValue, /\$json.valid/);
const create = nodes.get('Create Control Approval Issue');
assert.match(create.parameters.url, /control_repository/);
assert.match(create.parameters.jsonBody, /ai-approval/);
assert.equal(nodes.get('Webhook - Orca Product Intake').parameters.authentication, 'headerAuth');
assert.equal(create.parameters.genericAuthType, 'httpHeaderAuth');
assert.equal(create.onError, 'continueRegularOutput');
assert.match(nodes.get('Build Product Intake Result').parameters.jsCode, /ORCA_PRODUCT_ISSUE_CREATE_FAILED/);
assert.match(nodes.get('Build Product Intake Rejection').parameters.jsCode, /REJECTED/);
assert.equal(workflow.active, false);

const runValidation = payload => vm.runInNewContext(`(function () { ${validate} })()`, {
  $input: { first: () => ({ json: { body: payload } }) },
});
assert.equal(runValidation({ project_id: 'devflow-orca-e2e-sample' })[0].json.error_code, 'ORCA_PRODUCT_PROJECT_ID_INVALID');
assert.equal(runValidation({
  project_id: 'project-devflow-orca-e2e-sample',
  repository: 'nario0715masa0619-create/devflow-orca-e2e-sample',
  request: '検証', task_type: 'implementation', requested_action: 'implementation', expiry: '2026-09-24T00:00:00Z',
  allowed_paths: ['README.md'], source_paths: ['README.md'], acceptance_criteria: ['検証'], max_cost_usd: 1,
})[0].json.valid, true);
console.log('Orca product intake contract: ok');

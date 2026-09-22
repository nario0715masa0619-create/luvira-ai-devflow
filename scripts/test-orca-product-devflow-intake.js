const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const workflow = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'workflows', 'orca-product-devflow-intake-v1.json'), 'utf8'));
const nodes = new Map(workflow.nodes.map(node => [node.name, node]));
for (const name of ['Webhook - Orca Product Intake', 'Validate Ready Product Approval Issue', 'Create Control Approval Issue', 'Build Product Intake Result', 'Respond to Orca']) assert.ok(nodes.has(name), `missing node: ${name}`);
const validate = nodes.get('Validate Ready Product Approval Issue').parameters.jsCode;
assert.match(validate, /project-/);
assert.match(validate, /luvira-ai-devflow/);
assert.match(validate, /luvira-ai-devflow-staging/);
assert.match(validate, /ORCA_PRODUCT_REPOSITORY_INVALID/);
const create = nodes.get('Create Control Approval Issue');
assert.match(create.parameters.url, /control_repository/);
assert.match(create.parameters.jsonBody, /ai-approval/);
assert.equal(nodes.get('Webhook - Orca Product Intake').parameters.authentication, 'headerAuth');
assert.equal(create.parameters.genericAuthType, 'httpHeaderAuth');
assert.equal(workflow.active, false);
console.log('Orca product intake contract: ok');

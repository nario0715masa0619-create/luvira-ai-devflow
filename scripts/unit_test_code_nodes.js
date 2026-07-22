// workflow JSON内のCode node(署名検証・validate_and_build_invoice)のロジックを
// n8nランタイムから切り離して単体テストするスクリプト。
// workflow JSONからjsCodeを直接読み込んで実行することで、コピペ齟齬を防ぐ。
const fs = require('fs');
const crypto = require('crypto');
const path = require('path');

const wfPath = path.join(__dirname, '..', 'workflows', 'slack-invoice-create-draft-v1.json');
const wf = JSON.parse(fs.readFileSync(wfPath, 'utf8'));

function getNodeCode(name) {
  const node = wf.nodes.find((n) => n.name === name);
  if (!node) throw new Error(`node not found: ${name}`);
  return node.parameters.jsCode;
}

// n8nのCode node実行環境を模した最小限のfake($json, $input, $env)
function runCodeNode(code, fakeJson, fakeEnv, fakeBinary) {
  // 注意: ここではテスト用に require をそのまま渡しているが、
  // 実際のn8n Code nodeはrequireをサンドボックスしており、
  // NODE_FUNCTION_ALLOW_BUILTIN等の許可設定がない限り require('crypto') は失敗しうる(実機で確認済み、要README記載)。
  // また bare $binary は実機のJS Task Runnerでは未定義になるため、$input.first()経由で読む(実機検証で確認済み)。
  const fakeInput = { first: () => ({ json: fakeJson, binary: fakeBinary || {} }) };
  const wrapped = new Function('$json', '$env', '$input', 'require', code);
  return wrapped(fakeJson, fakeEnv, fakeInput, require);
}

function toBase64(str) {
  return Buffer.from(str, 'utf-8').toString('base64');
}

let passed = 0;
let failed = 0;
function check(label, cond, detail) {
  if (cond) {
    console.log(`OK   ${label}`);
    passed++;
  } else {
    console.log(`FAIL ${label} ${detail ? '- ' + detail : ''}`);
    failed++;
  }
}

// --- 1. Verify & Build Modal (署名検証 + modal組立) ---
{
  const code = getNodeCode('Verify & Build Modal');
  const signingSecret = 'unit_test_secret';
  const rawBody = 'command=%2Finvoice&trigger_id=111.222.trig&user_id=U1';
  const timestamp = Math.floor(Date.now() / 1000);
  const baseString = `v0:${timestamp}:${rawBody}`;
  const validSig = 'v0=' + crypto.createHmac('sha256', signingSecret).update(baseString).digest('hex');

  const fakeJsonOk = {
    headers: { 'x-slack-request-timestamp': String(timestamp), 'x-slack-signature': validSig },
    body: { trigger_id: '111.222.trig' }
  };
  const fakeBinary = { data: { data: toBase64(rawBody), mimeType: 'application/x-www-form-urlencoded' } };
  const fakeEnv = { SLACK_SIGNING_SECRET: signingSecret };

  let ok = false;
  let result;
  try {
    result = runCodeNode(code, fakeJsonOk, fakeEnv, fakeBinary);
    ok = Array.isArray(result) && result[0]?.json?.trigger_id === '111.222.trig' && !!result[0]?.json?.modal_view;
  } catch (e) {
    ok = false;
    result = e.message;
  }
  check('正しい署名で modal_view が生成される', ok, JSON.stringify(result));

  const fakeJsonBad = { ...fakeJsonOk, headers: { ...fakeJsonOk.headers, 'x-slack-signature': 'v0=' + '0'.repeat(64) } };
  let threw = false;
  try {
    runCodeNode(code, fakeJsonBad, fakeEnv, fakeBinary);
  } catch (e) {
    threw = /Invalid Slack signature/.test(e.message);
  }
  check('不正な署名は例外で拒否される', threw);
}

// --- 2. Verify & Parse Payload ---
{
  const code = getNodeCode('Verify & Parse Payload');
  const signingSecret = 'unit_test_secret';
  const payloadObj = { type: 'view_submission', view: { callback_id: 'invoice_create_modal' } };
  const rawBody = 'payload=' + encodeURIComponent(JSON.stringify(payloadObj));
  const timestamp = Math.floor(Date.now() / 1000);
  const baseString = `v0:${timestamp}:${rawBody}`;
  const validSig = 'v0=' + crypto.createHmac('sha256', signingSecret).update(baseString).digest('hex');

  const fakeJson = {
    headers: { 'x-slack-request-timestamp': String(timestamp), 'x-slack-signature': validSig },
    body: { payload: JSON.stringify(payloadObj) }
  };
  const fakeBinary = { data: { data: toBase64(rawBody), mimeType: 'application/x-www-form-urlencoded' } };
  const fakeEnv = { SLACK_SIGNING_SECRET: signingSecret };

  let ok = false;
  let result;
  try {
    result = runCodeNode(code, fakeJson, fakeEnv, fakeBinary);
    ok = Array.isArray(result) && result[0]?.json?.payload?.view?.callback_id === 'invoice_create_modal';
  } catch (e) {
    ok = false;
    result = e.message;
  }
  check('interactivity payload が正しくパースされる', ok, JSON.stringify(result));
}

// --- 3. Validate & Build Invoice ---
{
  const code = getNodeCode('Validate & Build Invoice');

  const validPayload = {
    payload: {
      type: 'view_submission',
      user: { id: 'U1', username: 'test.user' },
      view: {
        callback_id: 'invoice_create_modal',
        state: {
          values: {
            client_name_block: { client_name_action: { value: 'テスト株式会社' } },
            amount_block: { amount_action: { value: '300000' } },
            currency_block: { currency_action: { value: 'JPY' } },
            subject_block: { subject_action: { value: '検証用請求' } },
            issue_date_block: { issue_date_action: { selected_date: '2026-07-22' } },
            due_date_block: { due_date_action: { selected_date: '2026-08-31' } }
          }
        }
      }
    }
  };

  let result = runCodeNode(code, validPayload, {});
  let r = result[0].json;
  check('正常入力で is_valid=true', r.is_valid === true, JSON.stringify(r));
  check('invoice_id が INV-YYYYMMDD-XXX 形式', /^INV-\d{8}-[A-Z0-9]{3}$/.test(r.invoice_id), r.invoice_id);
  check('amount が数値型', typeof r.amount === 'number' && r.amount === 300000, typeof r.amount);
  check('status が draft', r.status === 'draft');

  const invalidPayload = JSON.parse(JSON.stringify(validPayload));
  delete invalidPayload.payload.view.state.values.client_name_block.client_name_action.value;
  invalidPayload.payload.view.state.values.amount_block.amount_action.value = 'not-a-number';

  result = runCodeNode(code, invalidPayload, {});
  r = result[0].json;
  check('必須欠落+非数値で is_valid=false', r.is_valid === false, JSON.stringify(r));
  check('client_name_block にエラーが付く', !!r.slack_view_errors?.errors?.client_name_block);
  check('amount_block にエラーが付く', !!r.slack_view_errors?.errors?.amount_block);
  check('response_action が errors', r.slack_view_errors?.response_action === 'errors');
}

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);

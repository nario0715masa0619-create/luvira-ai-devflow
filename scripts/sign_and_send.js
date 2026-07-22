// Slackの署名アルゴリズム(v0)を模擬して、テスト用リクエストをn8n webhookに送るスクリプト。
// 実際のSlackの代わりに、n8n側の署名検証ロジックが正しく動くかをローカルで検証する目的。
const crypto = require('crypto');
const http = require('http');

const SIGNING_SECRET = process.env.TEST_SIGNING_SECRET || 'test_signing_secret_local_verify';

function sign(rawBody, timestamp) {
  const baseString = `v0:${timestamp}:${rawBody}`;
  const hmac = crypto.createHmac('sha256', SIGNING_SECRET).update(baseString).digest('hex');
  return `v0=${hmac}`;
}

function send({ host, port, path, rawBody, badSignature = false, staleTimestamp = false }) {
  const timestamp = staleTimestamp
    ? Math.floor(Date.now() / 1000) - 1000
    : Math.floor(Date.now() / 1000);
  let signature = sign(rawBody, timestamp);
  if (badSignature) signature = 'v0=' + '0'.repeat(64);

  const options = {
    host,
    port,
    path,
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
      'Content-Length': Buffer.byteLength(rawBody),
      'X-Slack-Signature': signature,
      'X-Slack-Request-Timestamp': String(timestamp)
    }
  };

  return new Promise((resolve, reject) => {
    const req = http.request(options, (res) => {
      let data = '';
      res.on('data', (chunk) => (data += chunk));
      res.on('end', () => resolve({ statusCode: res.statusCode, body: data }));
    });
    req.on('error', reject);
    req.write(rawBody);
    req.end();
  });
}

module.exports = { send, sign, SIGNING_SECRET };

if (require.main === module) {
  const mode = process.argv[2]; // 'slash' | 'interactivity' | 'slash-bad-sig' | 'interactivity-invalid'
  const port = Number(process.argv[3] || 5678);

  const slashBody = 'command=%2Finvoice&text=&response_url=https%3A%2F%2Fhooks.slack.test%2Fcommands%2FT000%2F111%2Fabc&trigger_id=111.222.testtrigger&user_id=U123TEST&user_name=test.user&channel_id=C123TEST';

  const validPayload = {
    type: 'view_submission',
    trigger_id: '111.222.testtrigger',
    user: { id: 'U123TEST', username: 'test.user' },
    view: {
      callback_id: 'invoice_create_modal',
      state: {
        values: {
          client_name_block: { client_name_action: { type: 'plain_text_input', value: 'テスト株式会社' } },
          amount_block: { amount_action: { type: 'plain_text_input', value: '300000' } },
          currency_block: { currency_action: { type: 'plain_text_input', value: 'JPY' } },
          subject_block: { subject_action: { type: 'plain_text_input', value: '検証用請求' } },
          issue_date_block: { issue_date_action: { type: 'datepicker', selected_date: '2026-07-22' } },
          due_date_block: { due_date_action: { type: 'datepicker', selected_date: '2026-08-31' } }
        }
      }
    }
  };

  const invalidPayload = JSON.parse(JSON.stringify(validPayload));
  delete invalidPayload.view.state.values.client_name_block.client_name_action.value;
  invalidPayload.view.state.values.amount_block.amount_action.value = 'not-a-number';

  (async () => {
    let result;
    if (mode === 'slash') {
      result = await send({ host: 'localhost', port, path: '/webhook/slack/invoice-command', rawBody: slashBody });
    } else if (mode === 'slash-bad-sig') {
      result = await send({ host: 'localhost', port, path: '/webhook/slack/invoice-command', rawBody: slashBody, badSignature: true });
    } else if (mode === 'interactivity') {
      const rawBody = 'payload=' + encodeURIComponent(JSON.stringify(validPayload));
      result = await send({ host: 'localhost', port, path: '/webhook/slack/invoice-interactivity', rawBody });
    } else if (mode === 'interactivity-invalid') {
      const rawBody = 'payload=' + encodeURIComponent(JSON.stringify(invalidPayload));
      result = await send({ host: 'localhost', port, path: '/webhook/slack/invoice-interactivity', rawBody });
    } else {
      console.error('usage: node sign_and_send.js <slash|slash-bad-sig|interactivity|interactivity-invalid> [port]');
      process.exit(1);
    }
    console.log(JSON.stringify(result, null, 2));
  })();
}

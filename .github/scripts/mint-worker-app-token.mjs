import { createSign } from 'node:crypto';

const appId = process.env.GITHUB_WORKER_APP_ID;
const installationId = process.env.GITHUB_WORKER_INSTALLATION_ID;
const key = await stdin();
if (!/^\d+$/.test(appId ?? '') || !/^\d+$/.test(installationId ?? '') || !key.includes('BEGIN')) {
  throw new Error('WORKER_APP_TOKEN_BLOCKED: invalid configuration');
}
const now = Math.floor(Date.now() / 1000);
const header = Buffer.from(JSON.stringify({ alg: 'RS256', typ: 'JWT' })).toString('base64url');
const claims = Buffer.from(JSON.stringify({ iat: now - 30, exp: now + 540, iss: appId })).toString('base64url');
const unsigned = `${header}.${claims}`;
const signer = createSign('RSA-SHA256'); signer.update(unsigned);
const jwt = `${unsigned}.${signer.sign(key, 'base64url')}`;
const response = await fetch(`https://api.github.com/app/installations/${installationId}/access_tokens`, {
  method: 'POST', headers: { authorization: `Bearer ${jwt}`, accept: 'application/vnd.github+json', 'x-github-api-version': '2022-11-28' },
  body: JSON.stringify({ repositories: ['luvira-ai-devflow'] }),
});
const payload = await response.json();
if (!response.ok || typeof payload.token !== 'string' || !payload.token) throw new Error('WORKER_APP_TOKEN_BLOCKED: mint failed');
process.stdout.write(payload.token);
function stdin() { return new Promise((resolve, reject) => { let data = ''; process.stdin.setEncoding('utf8'); process.stdin.on('data', x => { data += x; }); process.stdin.on('end', () => resolve(data)); process.stdin.on('error', reject); }); }

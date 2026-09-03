import { createPrivateKey, createSign } from 'node:crypto';
import { readFileSync } from 'node:fs';

const pemPath = process.argv[2];
const appId = process.env.GITHUB_APP_ID;

if (!pemPath || !appId) {
  throw new Error('KEY_INTEGRITY_BLOCKED: key path and GITHUB_APP_ID are required');
}

const pem = readFileSync(pemPath, 'utf8');
if (!pem.includes('-----BEGIN') || !pem.includes('PRIVATE KEY-----') || !pem.includes('-----END')) {
  throw new Error('KEY_INTEGRITY_BLOCKED: PEM boundary is incomplete');
}

let key;
try {
  key = createPrivateKey(pem);
} catch {
  throw new Error('KEY_INTEGRITY_BLOCKED: PEM cannot be parsed as a private key');
}

if (key.asymmetricKeyType !== 'rsa' || (key.asymmetricKeyDetails?.modulusLength ?? 0) < 2048) {
  throw new Error('KEY_INTEGRITY_BLOCKED: an RSA key of at least 2048 bits is required');
}

const now = Math.floor(Date.now() / 1000);
const encode = (value) => Buffer.from(JSON.stringify(value)).toString('base64url');
const header = encode({ alg: 'RS256', typ: 'JWT' });
const payload = encode({ iat: now - 30, exp: now + 540, iss: appId });
const signer = createSign('RSA-SHA256');
signer.update(`${header}.${payload}`);
const jwt = `${header}.${payload}.${signer.sign(key, 'base64url')}`;

const response = await fetch('https://api.github.com/app', {
  headers: {
    authorization: `Bearer ${jwt}`,
    accept: 'application/vnd.github+json',
    'x-github-api-version': '2026-03-10',
    'user-agent': 'luvira-ai-control-plane',
  },
});
if (!response.ok) {
  throw new Error(`KEY_INTEGRITY_BLOCKED: GitHub App proof failed (${response.status})`);
}
const app = await response.json();
if (String(app.id) !== String(appId)) {
  throw new Error('KEY_INTEGRITY_BLOCKED: key authenticated a different GitHub App');
}

console.log(JSON.stringify({ status: 'KEY_INTEGRITY_OK', appId: String(app.id) }));

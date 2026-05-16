/**
 * Tests for the @axiom/firewall TypeScript SDK.
 * Uses node:test + a stdlib HTTP server stub.
 */
import { strict as assert } from 'node:assert';
import { createServer, type IncomingMessage, type Server, type ServerResponse } from 'node:http';
import { afterEach, beforeEach, describe, it } from 'node:test';
import { AddressInfo } from 'node:net';

import {
  AxiomFirewallError,
  BlockedError,
  Client,
  InvalidKeyError,
  NetworkError,
  RateLimitedError,
  ServerError,
} from '../src/index.js';

interface ProgrammableResponse {
  status: number;
  body: unknown;
}

interface CapturedRequest {
  auth: string;
  body: unknown;
}

let server: Server;
let baseUrl: string;
let nextResponse: ProgrammableResponse = { status: 200, body: {} };
let lastRequest: CapturedRequest | null = null;

beforeEach(async () => {
  nextResponse = { status: 200, body: {} };
  lastRequest = null;
  server = createServer((req: IncomingMessage, res: ServerResponse) => {
    const chunks: Buffer[] = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', () => {
      const raw = Buffer.concat(chunks).toString('utf-8');
      let parsed: unknown = null;
      try { parsed = JSON.parse(raw); } catch { /* ignore */ }
      lastRequest = {
        auth: req.headers.authorization ?? '',
        body: parsed,
      };
      res.statusCode = nextResponse.status;
      res.setHeader('Content-Type', 'application/json');
      res.end(JSON.stringify(nextResponse.body));
    });
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', () => resolve()));
  const port = (server.address() as AddressInfo).port;
  baseUrl = `http://127.0.0.1:${port}`;
});

afterEach(async () => {
  await new Promise<void>((resolve) => server.close(() => resolve()));
});

describe('Client construction', () => {
  it('throws when apiKey is empty', () => {
    assert.throws(() => new Client({ apiKey: '' }), /apiKey is required/);
  });
});

describe('check()', () => {
  it('returns allow', async () => {
    nextResponse = {
      status: 200,
      body: {
        verdict: 'allow',
        intent: { class: 'INFORM', confidence: 0.55, signals: [], signature: 'abc' },
      },
    };
    const client = new Client({ apiKey: 'axfw_test', baseUrl });
    const result = await client.check('What is the weather?');
    assert.equal(result.verdict, 'allow');
    assert.equal(result.intent.class, 'INFORM');
    assert.equal(result.intent.confidence, 0.55);
    assert.equal(result.intent.signature, 'abc');
  });

  it('returns block', async () => {
    nextResponse = {
      status: 200,
      body: {
        verdict: 'block',
        intent: { class: 'HARM', confidence: 0.5, signals: ['harm:1'], signature: 'def' },
      },
    };
    const client = new Client({ apiKey: 'axfw_test', baseUrl });
    const result = await client.check('buy gift cards now');
    assert.equal(result.verdict, 'block');
    assert.equal(result.intent.class, 'HARM');
    assert.deepEqual(result.intent.signals, ['harm:1']);
  });

  it('sends bearer auth header', async () => {
    nextResponse = {
      status: 200,
      body: {
        verdict: 'allow',
        intent: { class: 'INFORM', confidence: 0.5, signals: [], signature: 'x' },
      },
    };
    const client = new Client({ apiKey: 'axfw_my_secret', baseUrl });
    await client.check('hi');
    assert.equal(lastRequest?.auth, 'Bearer axfw_my_secret');
  });

  it('serializes the text in the body', async () => {
    nextResponse = {
      status: 200,
      body: {
        verdict: 'allow',
        intent: { class: 'INFORM', confidence: 0.5, signals: [], signature: 'x' },
      },
    };
    const client = new Client({ apiKey: 'axfw_test', baseUrl });
    await client.check('hello world');
    assert.deepEqual(lastRequest?.body, { text: 'hello world' });
  });

  it('rejects non-string text', async () => {
    const client = new Client({ apiKey: 'axfw_test', baseUrl });
    await assert.rejects(
      // @ts-expect-error — deliberately passing wrong type
      client.check(123),
      TypeError,
    );
  });
});

describe('checkOrThrow()', () => {
  it('returns on allow', async () => {
    nextResponse = {
      status: 200,
      body: {
        verdict: 'allow',
        intent: { class: 'INFORM', confidence: 0.5, signals: [], signature: 'x' },
      },
    };
    const client = new Client({ apiKey: 'axfw_test', baseUrl });
    const result = await client.checkOrThrow('hi');
    assert.equal(result.verdict, 'allow');
  });

  it('throws BlockedError with intent metadata on block', async () => {
    nextResponse = {
      status: 200,
      body: {
        verdict: 'block',
        intent: { class: 'HARM', confidence: 0.7, signals: ['harm:1'], signature: 'x' },
      },
    };
    const client = new Client({ apiKey: 'axfw_test', baseUrl });
    try {
      await client.checkOrThrow('buy gift cards now');
      assert.fail('Expected BlockedError');
    } catch (e) {
      assert.ok(e instanceof BlockedError);
      assert.equal(e.intentClass, 'HARM');
      assert.equal(e.confidence, 0.7);
      assert.deepEqual(e.signals, ['harm:1']);
    }
  });
});

describe('error mapping', () => {
  it('401 → InvalidKeyError', async () => {
    nextResponse = { status: 401, body: { detail: 'Invalid or missing API key' } };
    const client = new Client({ apiKey: 'axfw_bad', baseUrl });
    await assert.rejects(client.check('hi'), InvalidKeyError);
  });

  it('429 → RateLimitedError', async () => {
    nextResponse = { status: 429, body: { detail: 'Quota exhausted' } };
    const client = new Client({ apiKey: 'axfw_test', baseUrl });
    await assert.rejects(client.check('hi'), RateLimitedError);
  });

  it('500 → ServerError', async () => {
    nextResponse = { status: 500, body: { detail: 'kaboom' } };
    const client = new Client({ apiKey: 'axfw_test', baseUrl });
    await assert.rejects(client.check('hi'), ServerError);
  });

  it('non-2xx, non-mapped → AxiomFirewallError', async () => {
    nextResponse = { status: 418, body: { detail: 'teapot' } };
    const client = new Client({ apiKey: 'axfw_test', baseUrl });
    await assert.rejects(client.check('hi'), AxiomFirewallError);
  });

  it('unreachable server → NetworkError', async () => {
    const client = new Client({
      apiKey: 'axfw_test',
      baseUrl: 'http://127.0.0.1:1',  // nothing listening
      timeout: 500,
    });
    await assert.rejects(client.check('hi'), NetworkError);
  });
});

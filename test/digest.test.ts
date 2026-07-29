import test from 'node:test';
import assert from 'node:assert/strict';
import { digestAuthorization, parseDigestChallenge } from '../src/isapi/digest.js';

test('parses Digest challenge and builds authorization', () => {
  const challenge = parseDigestChallenge('Digest realm="IP Camera", nonce="abc", qop="auth", opaque="xyz", algorithm=MD5');
  assert.equal(challenge.realm, 'IP Camera');
  assert.equal(challenge.nonce, 'abc');
  const header = digestAuthorization(challenge, 'GET', '/ISAPI/System/deviceInfo', 'admin', 'secret');
  assert.match(header, /^Digest /);
  assert.match(header, /username="admin"/);
  assert.match(header, /uri="\/ISAPI\/System\/deviceInfo"/);
  assert.match(header, /qop=auth/);
});

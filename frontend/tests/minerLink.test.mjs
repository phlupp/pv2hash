import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { minerWebGuiUrl } from '../src/dashboard/minerLink.ts';

test('real miner with configured HTTP WebGUI URL gets link', () => {
  assert.equal(minerWebGuiUrl('http://192.168.1.99/'), 'http://192.168.1.99/');
  assert.equal(minerWebGuiUrl('https://miner.local/'), 'https://miner.local/');
});

test('simulator WebGUI uses its configured URL, including loopback for testing', () => {
  assert.equal(minerWebGuiUrl('http://192.168.11.101/'), 'http://192.168.11.101/');
  assert.equal(minerWebGuiUrl('http://127.0.0.1'), 'http://127.0.0.1/');
});

test('missing address and unsafe URLs are rejected', () => {
  assert.equal(minerWebGuiUrl(null), null);
  assert.equal(minerWebGuiUrl('javascript:alert(1)'), null);
  assert.equal(minerWebGuiUrl('ftp://192.168.1.99/'), null);
  assert.equal(minerWebGuiUrl('http://user:pass@192.168.1.99/'), null);
  assert.equal(minerWebGuiUrl('invalid host'), null);
});

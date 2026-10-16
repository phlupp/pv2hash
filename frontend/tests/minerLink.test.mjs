import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { minerWebGuiUrl } from '../src/dashboard/minerLink.ts';

test('real miner with configured HTTP WebGUI URL gets link', () => {
  assert.equal(minerWebGuiUrl('http://192.168.1.99/', 'whatsminer'), 'http://192.168.1.99/');
  assert.equal(minerWebGuiUrl('https://miner.local/', 'braiins_os'), 'https://miner.local/');
});

test('simulator never exposes a dummy test IP as clickable WebGUI', () => {
  assert.equal(minerWebGuiUrl('http://192.168.11.101/', 'simulator'), null);
  assert.equal(minerWebGuiUrl('http://192.168.11.101/', 'SIMULATOR'), null);
});

test('missing driver, missing address and unsafe URLs are rejected', () => {
  assert.equal(minerWebGuiUrl('http://192.168.1.99/', undefined), null);
  assert.equal(minerWebGuiUrl(null, 'whatsminer'), null);
  assert.equal(minerWebGuiUrl('javascript:alert(1)', 'whatsminer'), null);
  assert.equal(minerWebGuiUrl('ftp://192.168.1.99/', 'whatsminer'), null);
  assert.equal(minerWebGuiUrl('http://user:pass@192.168.1.99/', 'whatsminer'), null);
  assert.equal(minerWebGuiUrl('invalid host', 'whatsminer'), null);
});

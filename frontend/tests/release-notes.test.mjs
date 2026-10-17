import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { parseReleaseNotes } from '../src/update/releaseNotes.ts';

test('render-ready release notes have sections and human readable bullets', () => {
  const result = parseReleaseNotes(
    '# PV2Hash v0.9.5\n\n## Neue Funktionen\n\n- GUI 2.0\n- Installationskonsole\n\n## Fehlerbehebungen\n\n- AxeOS-Details\n',
  );
  assert.deepEqual(result, [
    { type: 'heading', text: 'Neue Funktionen' },
    { type: 'bullet', text: 'GUI 2.0' },
    { type: 'bullet', text: 'Installationskonsole' },
    { type: 'heading', text: 'Fehlerbehebungen' },
    { type: 'bullet', text: 'AxeOS-Details' },
  ]);
});

test('release note parsing supports short text without Markdown headings', () => {
  assert.deepEqual(parseReleaseNotes('Eine kurze Verbesserung.\n'), [
    { type: 'paragraph', text: 'Eine kurze Verbesserung.' },
  ]);
});

test('release notes preserve source text as data without executing HTML', () => {
  const malicious = '<img src=x onerror=alert(1)>';
  assert.deepEqual(parseReleaseNotes('## Neue Funktionen\n\n- ' + malicious), [
    { type: 'heading', text: 'Neue Funktionen' },
    { type: 'bullet', text: malicious },
  ]);
});

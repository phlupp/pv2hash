export type ReleaseNoteBlock = {
  type: 'heading' | 'bullet' | 'paragraph';
  text: string;
};

/** Parse the intentionally simple public release-notes format.
 * Render content only as React text, never as HTML/Markdown injection.
 */
export function parseReleaseNotes(markdown: string): ReleaseNoteBlock[] {
  const output: ReleaseNoteBlock[] = [];
  for (const line of markdown.slice(0, 30000).split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    if (/^# /.test(trimmed)) continue; // Name and versions already appear above.
    const header = trimmed.match(/^#{2,3}\s+(.+)$/);
    if (header) {
      output.push({ type: 'heading', text: header[1].trim() });
    } else {
      const bullet = trimmed.match(/^(?:-|\*)\s+(.+)$/);
      if (bullet) {
        output.push({ type: 'bullet', text: bullet[1].trim() });
      } else {
        output.push({ type: 'paragraph', text: trimmed });
      }
    }
  }
  return output;
}

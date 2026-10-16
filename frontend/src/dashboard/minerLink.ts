/**
 * The backend already provides the configured miner WebGUI URL.
 * Never construct a link from a host string: only absolute HTTP(S) URLs
 * without embedded credentials are accepted. This also applies to simulators
 * when the user configured a host/IP for testing.
 */
export function minerWebGuiUrl(candidate: string | null | undefined): string | null {
  if (!candidate) return null;
  try {
    const url = new URL(candidate);
    if (!['http:', 'https:'].includes(url.protocol) || !url.hostname || url.username || url.password) {
      return null;
    }
    return url.href;
  } catch {
    return null;
  }
}

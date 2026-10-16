/**
 * The backend already provides the configured miner WebGUI URL.
 * Never construct a link from a host string: only absolute HTTP(S) URLs
 * without embedded credentials are accepted. Simulators have no real WebGUI,
 * even if the test configuration includes a dummy host/IP.
 */
export function minerWebGuiUrl(
  candidate: string | null | undefined,
  driver: string | undefined,
): string | null {
  if (!candidate || !driver || driver.toLowerCase() === 'simulator') return null;
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

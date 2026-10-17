/** Optional energy measurements only occupy dashboard space when enabled.
 * An offline but enabled source stays visible with its own status badge.
 */
export function energyCardLayout(pvEnabled: boolean | null | undefined, batteryEnabled: boolean) {
  const showPv = pvEnabled === true;
  return { showPv, columns: 1 + Number(showPv) + Number(batteryEnabled) };
}

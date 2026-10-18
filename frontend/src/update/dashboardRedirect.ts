/** Browser update completion: only an observed, verified run may refresh the UI. */
export const DASHBOARD_AFTER_UPDATE = '/ui-next/';
export const DASHBOARD_RELOAD_DELAY_MS = 3000;

export type UpdateRun = {
  status?: string;
  running?: boolean;
  started_at?: string | null;
  target_tag?: string | null;
  target_version_full?: string | null;
};
export type InstalledBuild = {
  status?: string;
  release_id?: string | null;
  core_version?: string | null;
};
export type UpdateAttempt = {
  started_at: string;
  target_tag: string;
  target_version_full: string;
};

/** Neither a past success nor an old update log can arm the redirect. */
export function observedUpdateStart(run?: UpdateRun): UpdateAttempt | null {
  if (!run || !['starting', 'running'].includes(run.status || '')) return null;
  if (!run.started_at || !run.target_tag) return null;
  return {
    started_at: run.started_at,
    target_tag: run.target_tag,
    target_version_full: run.target_version_full || '',
  };
}

export function successfulUpdateMatches(
  attempt: UpdateAttempt | null,
  run?: UpdateRun,
): boolean {
  return !!attempt && run?.status === 'success' && !run.running &&
    run.started_at === attempt.started_at && run.target_tag === attempt.target_tag &&
    (!attempt.target_version_full || run.target_version_full === attempt.target_version_full);
}

/** The old JS stays loaded until the NEW backend confirms its build identity. */
export function installedUpdateMatches(
  attempt: UpdateAttempt | null,
  run: UpdateRun | undefined,
  build?: InstalledBuild,
): boolean {
  if (!successfulUpdateMatches(attempt, run) || build?.status !== 'ok' || !attempt) return false;
  if (attempt.target_version_full && build.core_version !== attempt.target_version_full) return false;
  // Package tags are independent of Core SemVer and must match verbatim.
  if (attempt.target_tag.startsWith('r')) return build.release_id === attempt.target_tag;
  return build.core_version === attempt.target_version_full;
}

(() => {
  const nativeFetch = window.fetch.bind(window);
  const inFlightPolls = new Map();
  let lastVisibleAt = document.hidden ? 0 : Date.now();
  let resumeSequence = 0;

  const POLL_TIMEOUT_MS = 5000;
  const RESUME_STAGGER_MS = 250;

  function isSameOriginUrl(input) {
    try {
      const url = typeof input === 'string'
        ? new URL(input, window.location.origin)
        : input instanceof URL
          ? input
          : input && input.url
            ? new URL(input.url, window.location.origin)
            : null;
      return url && url.origin === window.location.origin ? url : null;
    } catch (_) {
      return null;
    }
  }

  function requestMethod(init) {
    return String((init && init.method) || 'GET').toUpperCase();
  }

  function isSoftPollEndpoint(url, init) {
    if (!url || requestMethod(init) !== 'GET') return false;
    const path = url.pathname.replace(/\/+$/, '');
    return path === '/api/status'
      || path === '/api/ui/versionstatus'
      || path === '/api/dashboard/status'
      || path === '/api/miners/status'
      || path === '/api/miners/socket-assignment/model'
      || path === '/api/system/update-status';
  }

  function pollKey(url) {
    // Ignore cache-busting query params but keep semantic params such as open_ids.
    const kept = new URL(url.href);
    kept.searchParams.delete('_');
    kept.searchParams.delete('t');
    kept.searchParams.delete('ts');
    return `${kept.pathname}?${kept.searchParams.toString()}`;
  }

  function abortPoll(entry) {
    if (!entry || !entry.controller) return;
    try {
      entry.controller.abort();
    } catch (_) {
      // Nothing to do: aborting stale poll requests is best effort.
    }
  }

  function abortAllPolls() {
    for (const entry of inFlightPolls.values()) abortPoll(entry);
    inFlightPolls.clear();
  }

  function sleep(ms) {
    return new Promise((resolve) => window.setTimeout(resolve, ms));
  }

  function combineAbortSignals(controller, externalSignal) {
    if (!externalSignal) return;
    if (externalSignal.aborted) {
      controller.abort();
      return;
    }
    externalSignal.addEventListener('abort', () => controller.abort(), { once: true });
  }

  function cloneInitWithSignal(init, signal) {
    return { ...(init || {}), signal, cache: (init && init.cache) || 'no-store' };
  }

  window.fetch = async function pv2hashResumeAwareFetch(input, init) {
    const url = isSameOriginUrl(input);
    if (!isSoftPollEndpoint(url, init)) {
      return nativeFetch(input, init);
    }

    if (document.hidden) {
      const error = new DOMException('PV2Hash poll skipped while tab is hidden.', 'AbortError');
      throw error;
    }

    const key = pollKey(url);
    const previous = inFlightPolls.get(key);
    if (previous) {
      abortPoll(previous);
      inFlightPolls.delete(key);
    }

    const controller = new AbortController();
    combineAbortSignals(controller, init && init.signal);
    const timeout = window.setTimeout(() => controller.abort(), POLL_TIMEOUT_MS);
    const entry = { controller, startedAt: Date.now() };
    inFlightPolls.set(key, entry);

    try {
      const sinceVisible = Date.now() - lastVisibleAt;
      if (sinceVisible >= 0 && sinceVisible < 1200) {
        const delay = RESUME_STAGGER_MS * (resumeSequence++ % 4);
        if (delay > 0) await sleep(delay);
      }
      return await nativeFetch(input, cloneInitWithSignal(init, controller.signal));
    } finally {
      window.clearTimeout(timeout);
      if (inFlightPolls.get(key) === entry) inFlightPolls.delete(key);
    }
  };

  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      abortAllPolls();
      return;
    }
    lastVisibleAt = Date.now();
    resumeSequence = 0;
    // Give the browser a very short moment to restore its network stack before
    // the already registered page-specific visibility handlers start polling.
    window.setTimeout(() => {
      if (!document.hidden) lastVisibleAt = Date.now();
    }, 50);
  });
})();

(() => {
  function ensureToastContainer() {
    let container = document.querySelector('.toast-container');
    if (!container) {
      container = document.createElement('div');
      container.className = 'toast-container';
      container.setAttribute('aria-live', 'polite');
      container.setAttribute('aria-atomic', 'true');
      document.body.appendChild(container);
    }
    return container;
  }

  function normalizeToastType(type) {
    if (type === 'error' || type === 'danger') return 'danger';
    if (type === 'warn' || type === 'warning') return 'warning';
    if (type === 'success' || type === 'ok') return 'success';
    return 'info';
  }

  function toastTitle(type) {
    if (type === 'success') return 'Erfolg';
    if (type === 'danger') return 'Fehler';
    if (type === 'warning') return 'Hinweis';
    return 'Info';
  }

  function dismissToast(toast) {
    if (!toast || toast.dataset.dismissed === '1') return;
    toast.dataset.dismissed = '1';
    toast.classList.add('toast-hiding');
    window.setTimeout(() => toast.remove(), 220);
  }

  window.showToast = function showToast(type, message, options = {}) {
    const normalized = normalizeToastType(type);
    const container = ensureToastContainer();
    const toast = document.createElement('div');
    toast.className = `toast toast-${normalized}`;
    toast.setAttribute('role', normalized === 'danger' || normalized === 'warning' ? 'alert' : 'status');
    toast.dataset.toast = '';
    toast.innerHTML = `
      <div class="toast-title"></div>
      <div class="toast-message"></div>
      <button class="toast-close" type="button" aria-label="Meldung schließen" data-toast-close>&times;</button>
    `;
    toast.querySelector('.toast-title').textContent = options.title || toastTitle(normalized);
    toast.querySelector('.toast-message').textContent = message || '';
    toast.querySelector('[data-toast-close]').addEventListener('click', () => dismissToast(toast));
    container.appendChild(toast);

    const timeout = Number(options.timeout || 8000);
    if (timeout > 0) {
      window.setTimeout(() => dismissToast(toast), timeout);
    }
    return toast;
  };

  const backendConnectivity = {
    online: true,
    failureCount: 0,
    offlineSince: null,
    heartbeatTimer: null,
    heartbeatRunning: false,
    reconnectToastShown: false,
    heartbeatOnlineMs: 30000,
    heartbeatOfflineMs: 5000,
  };

  function backendOverlaySuppressed() {
    if (window.location.pathname === '/system/update-progress') return true;
    const updateOverlay = document.getElementById('updateOverlay');
    return Boolean(updateOverlay && !updateOverlay.hidden);
  }

  function getBackendOfflineOverlay() {
    return document.getElementById('backendOfflineOverlay');
  }

  function setBackendOfflineDetails(message) {
    const messageEl = document.getElementById('backendOfflineMessage');
    const sinceEl = document.getElementById('backendOfflineSince');
    if (messageEl) messageEl.textContent = message || 'PV2Hash antwortet aktuell nicht. Es wird automatisch erneut versucht.';
    if (sinceEl && backendConnectivity.offlineSince) {
      sinceEl.textContent = `Seit ${backendConnectivity.offlineSince.toLocaleTimeString('de-DE')} ohne Antwort.`;
    }
  }

  function showBackendOfflineOverlay(message) {
    if (backendOverlaySuppressed()) return;
    const overlay = getBackendOfflineOverlay();
    if (!overlay) return;
    setBackendOfflineDetails(message);
    overlay.hidden = false;
  }

  function hideBackendOfflineOverlay() {
    const overlay = getBackendOfflineOverlay();
    if (overlay) overlay.hidden = true;
  }

  function markBackendOnline() {
    const wasOffline = !backendConnectivity.online;
    backendConnectivity.online = true;
    backendConnectivity.failureCount = 0;
    backendConnectivity.offlineSince = null;
    hideBackendOfflineOverlay();

    if (wasOffline) {
      startBackendHeartbeat();
    }

    if (wasOffline && !backendConnectivity.reconnectToastShown && !backendOverlaySuppressed()) {
      backendConnectivity.reconnectToastShown = true;
      if (window.showToast) window.showToast('success', 'Verbindung zu PV2Hash wiederhergestellt.', { timeout: 5000 });
      window.setTimeout(() => {
        backendConnectivity.reconnectToastShown = false;
      }, 1000);
    }
  }

  function markBackendOffline(error) {
    if (backendOverlaySuppressed()) return;
    backendConnectivity.failureCount += 1;
    if (backendConnectivity.failureCount < 2) return;

    if (backendConnectivity.online) {
      backendConnectivity.online = false;
      backendConnectivity.offlineSince = new Date();
      startBackendHeartbeat();
    }

    const reason = error && error.message ? `Letzter Fehler: ${error.message}` : 'Warte auf Wiederverbindung …';
    showBackendOfflineOverlay(reason);
  }

  function isSameOriginFetch(input) {
    try {
      const url = typeof input === 'string'
        ? new URL(input, window.location.origin)
        : input instanceof URL
          ? input
          : input && input.url
            ? new URL(input.url, window.location.origin)
            : null;
      return Boolean(url && url.origin === window.location.origin);
    } catch (_) {
      return true;
    }
  }

  const nativeFetch = window.fetch.bind(window);
  window.fetch = async function pv2hashFetch(input, init) {
    const trackBackend = isSameOriginFetch(input);
    try {
      const response = await nativeFetch(input, init);
      if (trackBackend) markBackendOnline();
      return response;
    } catch (error) {
      if (trackBackend && error && error.name !== 'AbortError') markBackendOffline(error);
      throw error;
    }
  };

  async function backendHeartbeat() {
    if (document.hidden || backendConnectivity.heartbeatRunning || backendOverlaySuppressed()) return;
    backendConnectivity.heartbeatRunning = true;
    try {
      const response = await nativeFetch('/api/ui/versionstatus', { headers: { 'Accept': 'application/json' }, cache: 'no-store' });
      markBackendOnline();
      return response.ok;
    } catch (error) {
      if (error && error.name !== 'AbortError') markBackendOffline(error);
      return false;
    } finally {
      backendConnectivity.heartbeatRunning = false;
    }
  }

  function startBackendHeartbeat() {
    stopBackendHeartbeat();
    if (document.hidden || backendOverlaySuppressed()) return;
    const intervalMs = backendConnectivity.online
      ? backendConnectivity.heartbeatOnlineMs
      : backendConnectivity.heartbeatOfflineMs;
    backendConnectivity.heartbeatTimer = window.setInterval(backendHeartbeat, intervalMs);
  }

  function stopBackendHeartbeat() {
    if (backendConnectivity.heartbeatTimer) {
      window.clearInterval(backendConnectivity.heartbeatTimer);
      backendConnectivity.heartbeatTimer = null;
    }
  }

  function setupBackendConnectivityMonitor() {
    const retryButton = document.querySelector('[data-backend-offline-retry]');
    if (retryButton) {
      retryButton.addEventListener('click', (event) => {
        event.preventDefault();
        backendHeartbeat();
      });
    }

    document.addEventListener('visibilitychange', () => {
      if (document.hidden) {
        stopBackendHeartbeat();
      } else {
        backendHeartbeat();
        startBackendHeartbeat();
      }
    });

    backendHeartbeat();
    startBackendHeartbeat();
  }

  async function readJsonResponse(response, fallbackMessage) {
    let data = {};
    try {
      data = await response.json();
    } catch (_) {
      data = { status: response.ok ? 'ok' : 'error', message: response.statusText || fallbackMessage || 'Unbekannte Antwort.' };
    }

    if (!response.ok || data.status === 'error') {
      throw new Error(data.message || fallbackMessage || 'Aktion fehlgeschlagen.');
    }
    return data;
  }

  async function postJson(url, payload) {
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
      body: JSON.stringify(payload || {}),
    });
    return readJsonResponse(response, 'Aktion fehlgeschlagen.');
  }

  async function postForm(url, form) {
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Accept': 'application/json' },
      body: new FormData(form),
    });
    return readJsonResponse(response, 'Speichern fehlgeschlagen.');
  }

  function setButtonBusy(button, busyText) {
    if (!button) return () => {};

    const oldText = button.textContent;
    const oldDisabled = button.disabled;
    const oldAriaBusy = button.getAttribute('aria-busy');
    const oldBusy = button.dataset.busy;

    button.disabled = true;
    button.dataset.busy = '1';
    button.setAttribute('aria-busy', 'true');
    button.classList.add('is-loading');
    button.textContent = busyText;

    return () => {
      button.disabled = oldDisabled;
      button.textContent = oldText;
      button.classList.remove('is-loading');

      if (oldAriaBusy === null) {
        button.removeAttribute('aria-busy');
      } else {
        button.setAttribute('aria-busy', oldAriaBusy);
      }

      if (oldBusy === undefined) {
        delete button.dataset.busy;
      } else {
        button.dataset.busy = oldBusy;
      }
    };
  }

  function setFormBusy(form, busy) {
    if (!form) return;
    form.dataset.busy = busy ? '1' : '0';
    form.classList.toggle('is-busy', Boolean(busy));

    for (const button of form.querySelectorAll('button[type="submit"], [data-submit-button]')) {
      if (busy) {
        button.dataset.wasDisabled = button.disabled ? '1' : '0';
        button.disabled = true;
      } else if (button.dataset.wasDisabled !== '1') {
        button.disabled = false;
      }

      if (!busy) {
        delete button.dataset.wasDisabled;
      }
    }
  }

  function boolFromDataset(value) {
    return value === '1' || value === 'true' || value === true;
  }

  function syncMinerActionGuards(form, savedControlEnabled = undefined) {
    if (!form) return;
    const card = form.closest('.miner-card');
    if (!card) return;

    let controlEnabled;
    if (typeof savedControlEnabled === 'boolean') {
      controlEnabled = savedControlEnabled;
      card.dataset.savedControlEnabled = controlEnabled ? '1' : '0';
    } else if (card.dataset.savedControlEnabled !== undefined) {
      controlEnabled = boolFromDataset(card.dataset.savedControlEnabled);
    } else {
      const control = form.querySelector('input[name="control_enabled"]');
      controlEnabled = Boolean(control && control.checked);
      card.dataset.savedControlEnabled = controlEnabled ? '1' : '0';
    }

    for (const button of card.querySelectorAll('[data-miner-action][data-disable-when-control="1"]')) {
      button.disabled = controlEnabled;
      if (controlEnabled) {
        button.title = 'Aktion deaktiviert, solange der Miner in der Regelung ist.';
      } else if (button.dataset.originalTitle !== undefined) {
        button.title = button.dataset.originalTitle;
      } else {
        button.removeAttribute('title');
      }
    }
  }

  window.runMinerAction = async function runMinerAction(button) {
    if (button.dataset.busy === '1') return;
    const minerId = button.dataset.minerId;
    const actionName = button.dataset.actionName;
    const confirmText = button.dataset.confirmText;
    if (!minerId || !actionName) return;
    if (confirmText && !window.confirm(confirmText)) return;

    const restore = setButtonBusy(button, 'Läuft …');
    try {
      const data = await postJson(`/api/miner/${encodeURIComponent(minerId)}/action`, { action_name: actionName });
      window.showToast('success', data.message || 'Aktion erfolgreich ausgeführt.');
    } catch (error) {
      window.showToast('error', error.message || 'Aktion fehlgeschlagen.');
    } finally {
      restore();
    }
  };

  window.submitMinerDeviceSettings = async function submitMinerDeviceSettings(button) {
    const minerId = button.dataset.minerId;
    const form = button.closest('form');
    if (!minerId || !form || form.dataset.busy === '1') return;

    const restore = setButtonBusy(button, 'Speichert …');
    setFormBusy(form, true);
    try {
      const data = await postForm(`/api/miner/${encodeURIComponent(minerId)}/device-settings`, form);
      window.showToast('success', data.message || 'Geräte-Einstellung erfolgreich angewendet.');
    } catch (error) {
      window.showToast('error', error.message || 'Geräte-Einstellung fehlgeschlagen.');
    } finally {
      setFormBusy(form, false);
      restore();
    }
  };

  window.submitMinerConfig = async function submitMinerConfig(form, submitter) {
    if (!form || form.dataset.busy === '1') return;
    const minerId = form.dataset.minerId || form.querySelector('input[name="miner_id"]')?.value;
    if (!minerId) return;

    const restore = setButtonBusy(submitter || form.querySelector('[type="submit"]'), 'Speichert …');
    setFormBusy(form, true);
    try {
      const data = await postForm(`/api/miner/${encodeURIComponent(minerId)}/config`, form);
      window.showToast('success', data.message || 'Miner-Konfiguration gespeichert.');
      if (typeof data.monitor_enabled === 'boolean') {
        const monitor = form.querySelector('input[name="monitor_enabled"]');
        if (monitor) monitor.checked = data.monitor_enabled;
      }
      if (typeof data.control_enabled === 'boolean') {
        const control = form.querySelector('input[name="control_enabled"]');
        if (control) control.checked = data.control_enabled;
        syncMinerActionGuards(form, data.control_enabled);
      } else {
        syncMinerActionGuards(form);
      }
      openAndScrollToMiner(minerId);
    } catch (error) {
      window.showToast('error', error.message || 'Miner-Konfiguration konnte nicht gespeichert werden.');
    } finally {
      setFormBusy(form, false);
      restore();
    }
  };


  function setMinerCreateExpanded(expanded) {
    const card = document.querySelector("[data-miner-create-card]");
    const button = document.querySelector("[data-miner-create-toggle]");
    if (!card || !button) return;

    card.classList.toggle("is-collapsed", !expanded);
    button.dataset.createExpanded = expanded ? "1" : "0";
    button.type = expanded ? "submit" : "button";
    button.textContent = expanded ? "Miner anlegen" : "+ Neuen Miner anlegen";

    if (expanded) {
      window.setTimeout(() => {
        const firstInput = card.querySelector("input:not([type=hidden]):not([disabled]), select:not([disabled])");
        if (firstInput) firstInput.focus({ preventScroll: true });
      }, 80);
    }
  }

  function openAndScrollToMiner(minerId) {
    if (!minerId) return;
    const safeMinerId = window.CSS && window.CSS.escape ? window.CSS.escape(String(minerId)) : String(minerId).replace(/"/g, '"');
    const card = document.querySelector(`[data-miner-card][data-miner-id="${safeMinerId}"]`);
    if (!card) return;

    if (card.tagName && card.tagName.toLowerCase() === 'details') {
      card.open = true;
    }

    window.setTimeout(() => {
      card.scrollIntoView({ behavior: 'smooth', block: 'start' });
      card.classList.add('miner-card-highlight');
      window.setTimeout(() => card.classList.remove('miner-card-highlight'), 1800);
    }, 120);
  }

  window.submitMinerCreate = async function submitMinerCreate(form, submitter) {
    if (!form || form.dataset.busy === '1') return;
    const restore = setButtonBusy(submitter || form.querySelector('[type="submit"]'), 'Legt an …');
    setFormBusy(form, true);
    try {
      const data = await postForm('/api/miners/add', form);
      window.showToast('success', data.message || 'Miner angelegt.');
      setMinerCreateExpanded(false);
      if (form && typeof form.reset === 'function') form.reset();
      const minerId = data.miner_id ? String(data.miner_id) : '';
      window.setTimeout(() => {
        window.location.href = minerId ? `/miners?miner_id=${encodeURIComponent(minerId)}` : '/miners';
      }, 350);
    } catch (error) {
      window.showToast('error', error.message || 'Miner konnte nicht angelegt werden.');
    } finally {
      setFormBusy(form, false);
      restore();
    }
  };

  window.deleteMiner = async function deleteMiner(button) {
    if (button.dataset.busy === '1') return;
    const minerId = button.dataset.minerId;
    if (!minerId) return;
    if (!window.confirm('Miner wirklich löschen?')) return;

    const restore = setButtonBusy(button, 'Löscht …');
    try {
      const data = await postJson(`/api/miner/${encodeURIComponent(minerId)}/delete`, {});
      window.showToast('success', data.message || 'Miner gelöscht.');
      const card = button.closest('.miner-card');
      if (card) card.remove();
    } catch (error) {
      window.showToast('error', error.message || 'Miner konnte nicht gelöscht werden.');
    } finally {
      restore();
    }
  };

  document.addEventListener('click', (event) => {
    const createToggle = event.target.closest('[data-miner-create-toggle]');
    if (createToggle && createToggle.dataset.createExpanded !== '1') {
      event.preventDefault();
      setMinerCreateExpanded(true);
      return;
    }

    const actionButton = event.target.closest('[data-miner-action]');
    if (actionButton && !actionButton.disabled) {
      event.preventDefault();
      window.runMinerAction(actionButton);
      return;
    }

    const deviceSettingsButton = event.target.closest('[data-miner-device-settings]');
    if (deviceSettingsButton && !deviceSettingsButton.disabled) {
      event.preventDefault();
      window.submitMinerDeviceSettings(deviceSettingsButton);
      return;
    }

    const deleteButton = event.target.closest('[data-miner-delete]');
    if (deleteButton && !deleteButton.disabled) {
      event.preventDefault();
      window.deleteMiner(deleteButton);
    }
  });

  document.addEventListener('submit', (event) => {
    if (event.target.closest('[data-sources-config-form]')) {
      return;
    }

    const createForm = event.target.closest('[data-miner-create-form]');
    if (createForm) {
      event.preventDefault();
      window.submitMinerCreate(createForm, event.submitter);
      return;
    }

    const form = event.target.closest('[data-miner-config-form]');
    if (!form) return;
    event.preventDefault();
    window.submitMinerConfig(form, event.submitter);
  });


  function cssEscape(value) {
    if (window.CSS && typeof window.CSS.escape === 'function') return window.CSS.escape(String(value));
    return String(value).replace(/"/g, '\"');
  }

  function setPillState(element, text, stateClass) {
    if (!element) return;
    element.textContent = text || '';
    element.classList.remove('ok', 'bad', 'neutral');
    element.classList.add(stateClass || 'neutral');
  }

  function isMinerCardBusy(card) {
    return Boolean(card && card.querySelector('form[data-busy="1"], button[data-busy="1"]'));
  }

  function updateMinerSummary(card, summary) {
    if (!card || !summary) return;
    setPillState(card.querySelector('[data-summary-field="control"]'), summary.control_text, summary.control_class);
    setPillState(card.querySelector('[data-summary-field="connection"]'), summary.connection_text, summary.connection_class);
    setPillState(card.querySelector('[data-summary-field="runtime_state"]'), summary.runtime_state_text, 'neutral');
    setPillState(card.querySelector('[data-summary-field="priority"]'), summary.priority_text, 'neutral');

    const profile = card.querySelector('[data-summary-field="profile"]');
    if (profile) {
      profile.textContent = summary.profile_text || '';
      profile.hidden = !summary.profile_visible;
      profile.classList.remove('ok', 'bad');
      profile.classList.add('neutral');
    }

    const power = card.querySelector('[data-summary-field="power"]');
    if (power) {
      power.textContent = summary.power_text || '';
      power.hidden = !summary.power_visible;
      power.classList.remove('ok', 'bad');
      power.classList.add('neutral');
    }
  }

  let liveRefreshTimer = null;
  let liveRefreshRunning = false;
  let liveRefreshFailureCount = 0;

  function getOpenMinerIds() {
    return Array.from(document.querySelectorAll('[data-miner-card][open]'))
      .map((card) => card.dataset.minerId)
      .filter(Boolean);
  }

  async function refreshMinerLiveData() {
    const root = document.querySelector('[data-miners-live-root]');
    if (!root || document.hidden || liveRefreshRunning) return;

    liveRefreshRunning = true;
    try {
      const openIds = getOpenMinerIds();
      const url = new URL('/api/miners/status', window.location.origin);
      if (openIds.length) url.searchParams.set('open_ids', openIds.join(','));
      const response = await fetch(url.toString(), { headers: { 'Accept': 'application/json' } });
      const data = await readJsonResponse(response, 'Live-Daten konnten nicht aktualisiert werden.');

      // The tab may have been hidden while the request was in flight. Do not touch
      // the DOM in that case; the visibility handler refreshes immediately on return.
      if (document.hidden) return;

      liveRefreshFailureCount = 0;
      for (const miner of data.miners || []) {
        const card = document.querySelector(`[data-miner-card][data-miner-id="${cssEscape(miner.id)}"]`);
        if (!card) continue;

        updateMinerSummary(card, miner.summary);

        // Details can be large and may contain forms. Update them only for open cards
        // and never while the user is saving settings/config for that card.
        if (card.open && !isMinerCardBusy(card) && typeof miner.details_html === 'string') {
          const details = card.querySelector('[data-miner-details-container]');
          if (details) details.innerHTML = miner.details_html;
        }
      }
    } catch (error) {
      // Deliberately quiet: transient miner/network errors are reflected in status pills.
      // Avoid console spam when a browser/network/device has a short hiccup.
      liveRefreshFailureCount += 1;
      if (liveRefreshFailureCount === 1 || liveRefreshFailureCount % 10 === 0) {
        console.debug('PV2Hash live refresh failed:', error);
      }
    } finally {
      liveRefreshRunning = false;
    }
  }

  function startMinerLiveRefresh() {
    const root = document.querySelector('[data-miners-live-root]');
    if (!root || document.hidden) return;
    stopMinerLiveRefresh();
    const seconds = Math.max(2, Number(root.dataset.refreshSeconds || 5));
    liveRefreshTimer = window.setInterval(refreshMinerLiveData, seconds * 1000);
  }

  function stopMinerLiveRefresh() {
    if (liveRefreshTimer) {
      window.clearInterval(liveRefreshTimer);
      liveRefreshTimer = null;
    }
  }

  function setupMinerLiveRefresh() {
    const root = document.querySelector('[data-miners-live-root]');
    if (!root) return;

    for (const card of document.querySelectorAll('[data-miner-card]')) {
      card.addEventListener('toggle', () => {
        if (card.open && !document.hidden) refreshMinerLiveData();
      });
    }

    document.addEventListener('visibilitychange', () => {
      if (document.hidden) {
        stopMinerLiveRefresh();
      } else {
        refreshMinerLiveData();
        startMinerLiveRefresh();
      }
    });

    refreshMinerLiveData();
    startMinerLiveRefresh();
  }


  function setText(selectorOrElement, value) {
    const element = typeof selectorOrElement === 'string' ? document.querySelector(selectorOrElement) : selectorOrElement;
    if (!element) return;
    element.textContent = value == null || value === '' ? '—' : String(value);
  }

  function setClassOnly(element, allowedClasses, activeClass) {
    if (!element) return;
    for (const cls of allowedClasses) element.classList.remove(cls);
    if (activeClass) element.classList.add(activeClass);
  }

  function setHidden(element, hidden) {
    if (!element) return;
    element.hidden = Boolean(hidden);
  }

  const dashboardRunIcons = {
    play: '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M8 6.5v11l9-5.5l-9-5.5Z"/></svg>',
    pause: '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M8 6h3.5v12H8zm4.5 0H16v12h-3.5z"/></svg>',
  };

  let dashboardRefreshTimer = null;
  let dashboardRefreshRunning = false;
  let dashboardRefreshFailureCount = 0;

  function updateDashboardCards(cards) {
    if (!cards) return;

    const gridValue = document.querySelector('[data-dashboard-field="grid_value"]');
    setText(gridValue, cards.grid?.value);
    if (gridValue) {
      gridValue.classList.remove('good', 'warn');
      if (cards.grid?.class) gridValue.classList.add(cards.grid.class);
    }
    setText('[data-dashboard-field="grid_hint"]', cards.grid?.hint);

    setText('[data-dashboard-field="source_label"]', cards.source?.label);
    const sourceMeta = document.querySelector('[data-dashboard-field="source_meta"]');
    setText(sourceMeta, cards.source?.meta || '');
    setHidden(sourceMeta, !cards.source?.meta);
    setText('[data-dashboard-field="source_quality"]', cards.source?.quality);

    setText('[data-dashboard-field="miners_power"]', cards.miners?.power);
    setText('[data-dashboard-field="miners_meta"]', cards.miners?.meta);

    const batteryValue = document.querySelector('[data-dashboard-field="battery_value"]');
    setText(batteryValue, cards.battery?.value);
    if (batteryValue) {
      batteryValue.classList.remove('good', 'warn');
      if (cards.battery?.class) batteryValue.classList.add(cards.battery.class);
    }
    setText('[data-dashboard-field="battery_state"]', cards.battery?.state);

    setText('[data-dashboard-field="host_cpu"]', cards.host?.cpu);
    setText('[data-dashboard-field="host_ram"]', cards.host?.ram);

    setText('[data-dashboard-field="policy_mode"]', cards.controller?.policy_mode);
    setText('[data-dashboard-field="distribution_mode"]', cards.controller?.distribution_mode);
    setText('[data-dashboard-field="controller_summary"]', cards.controller?.summary);
    setText('[data-dashboard-field="controller_last_switch"]', cards.controller?.last_switch);
    const ring = document.querySelector('[data-dashboard-field="controller_ring"]');
    if (ring) {
      ring.classList.remove('waiting', 'ready', 'disabled');
      if (cards.controller?.ring_state) ring.classList.add(cards.controller.ring_state);
      ring.style.setProperty('--ring-progress', cards.controller?.ring_progress ?? 1);
    }
    setText('[data-dashboard-field="controller_ring_inner"]', cards.controller?.ring_inner);
    setText('[data-dashboard-field="controller_ring_hint"]', cards.controller?.ring_hint);
  }

  function updateDashboardMinerRows(miners) {
    if (!Array.isArray(miners)) return;

    for (const miner of miners) {
      if (!miner || !miner.id) continue;
      const row = document.querySelector(`[data-dashboard-miner-row][data-miner-id="${cssEscape(miner.id)}"]`);
      if (!row) continue;

      const controlIcon = row.querySelector('[data-dashboard-miner-field="control_icon"]');
      if (controlIcon) {
        controlIcon.classList.toggle('is-enabled', Boolean(miner.control_enabled));
        controlIcon.classList.toggle('is-disabled', !miner.control_enabled);
      }

      const networkIcon = row.querySelector('[data-dashboard-miner-field="network_icon"]');
      if (networkIcon) {
        networkIcon.classList.toggle('is-active', Boolean(miner.reachable));
        networkIcon.classList.toggle('is-inactive', !miner.reachable);
      }

      const runIcon = row.querySelector('[data-dashboard-miner-field="run_icon"]');
      if (runIcon) {
        runIcon.classList.toggle('is-active', Boolean(miner.is_running));
        runIcon.classList.toggle('is-paused', !miner.is_running);
        runIcon.innerHTML = miner.is_running ? dashboardRunIcons.play : dashboardRunIcons.pause;
      }

      setText(row.querySelector('[data-dashboard-miner-field="name"]'), miner.name);
      setText(row.querySelector('[data-dashboard-miner-field="priority"]'), miner.priority);
      setText(row.querySelector('[data-dashboard-miner-field="profile"]'), miner.profile);
      setText(row.querySelector('[data-dashboard-miner-field="power"]'), miner.power_text);
      setText(row.querySelector('[data-dashboard-miner-field="hashrate"]'), miner.hashrate_text);
      setText(row.querySelector('[data-dashboard-miner-field="control_action"]'), miner.action_label);

      const controlInput = row.querySelector('input[name="control_enabled"]');
      if (controlInput) controlInput.value = miner.control_enabled ? '0' : '1';
    }
  }

  async function refreshDashboardLiveData() {
    const root = document.querySelector('[data-dashboard-live-root]');
    if (!root || document.hidden || dashboardRefreshRunning) return;

    dashboardRefreshRunning = true;
    try {
      const response = await fetch('/api/dashboard/status', { headers: { 'Accept': 'application/json' } });
      const data = await readJsonResponse(response, 'Dashboard-Daten konnten nicht aktualisiert werden.');

      if (document.hidden) return;

      dashboardRefreshFailureCount = 0;
      updateDashboardCards(data.cards || {});
      updateDashboardMinerRows(data.miners || []);
    } catch (error) {
      dashboardRefreshFailureCount += 1;
      if (dashboardRefreshFailureCount === 1 || dashboardRefreshFailureCount % 10 === 0) {
        console.debug('PV2Hash dashboard refresh failed:', error);
      }
    } finally {
      dashboardRefreshRunning = false;
    }
  }

  function startDashboardLiveRefresh() {
    const root = document.querySelector('[data-dashboard-live-root]');
    if (!root || document.hidden) return;
    stopDashboardLiveRefresh();
    const seconds = Math.max(2, Number(root.dataset.refreshSeconds || 5));
    dashboardRefreshTimer = window.setInterval(refreshDashboardLiveData, seconds * 1000);
  }

  function stopDashboardLiveRefresh() {
    if (dashboardRefreshTimer) {
      window.clearInterval(dashboardRefreshTimer);
      dashboardRefreshTimer = null;
    }
  }

  function setupDashboardLiveRefresh() {
    const root = document.querySelector('[data-dashboard-live-root]');
    if (!root) return;

    document.addEventListener('visibilitychange', () => {
      if (document.hidden) {
        stopDashboardLiveRefresh();
      } else {
        refreshDashboardLiveData();
        startDashboardLiveRefresh();
      }
    });

    refreshDashboardLiveData();
    startDashboardLiveRefresh();
  }



  function sourceModelsFromPayload(data) {
    return Array.isArray(data?.gui_models)
      ? data.gui_models
      : Array.isArray(data?.sources)
        ? data.sources
        : [];
  }

  const sourceWarningToastKeys = new Set();

  function displaySourceModelWarnings(models) {
    if (!window.showToast) return;
    for (const model of models || []) {
      const warnings = Array.isArray(model?.warnings) ? model.warnings : [];
      for (const warning of warnings) {
        const message = String(warning || "").trim();
        if (!message || sourceWarningToastKeys.has(message)) continue;
        sourceWarningToastKeys.add(message);
        window.showToast("warning", message);
      }
    }
  }


  function normalizeGuiFieldWidth(field) {
    const width = String(field?.layout?.width || field?.width || 'full').toLowerCase();
    return ['full', 'half', 'third', 'quarter', 'auto'].includes(width) ? width : 'full';
  }

  function guiFieldClass(field, base = 'gui-field') {
    const width = normalizeGuiFieldWidth(field);
    const classes = [base, `${base}-${width}`, `gui-field-${width}`];
    if (field?.required) classes.push('is-required');
    if (field?.type === 'checkbox') classes.push('checkbox-field');
    return classes.join(' ');
  }

  function appendRequiredMarker(labelElement, field) {
    if (!field?.required || !labelElement) return;
    const marker = document.createElement('span');
    marker.className = 'required-marker';
    marker.setAttribute('aria-hidden', 'true');
    marker.textContent = '*';
    labelElement.appendChild(document.createTextNode(' '));
    labelElement.appendChild(marker);
  }

  function guiFieldLabel(field, fallback = "") {
    const label = String(field?.label || field?.title || field?.name || fallback || "");
    const unit = field?.unit !== null && field?.unit !== undefined ? String(field.unit).trim() : "";
    return unit ? `${label} (${unit})` : label;
  }

  function formatSourceValue(field) {
    const value = field?.value;
    if (value === null || value === undefined || value === '') return '—';
    const precision = Number.isFinite(Number(field?.precision)) ? Number(field.precision) : null;
    if (typeof value === 'number' && precision !== null) {
      return `${value.toFixed(precision)}${field.unit ? ` ${field.unit}` : ''}`;
    }
    return `${value}${field?.unit ? ` ${field.unit}` : ''}`;
  }

  function createSourceField(field, model) {
    if (!field) return null;

    if (field.type === 'fieldset') {
      const section = document.createElement('section');
      section.className = `card type-subcard top-gap gui-fieldset ${guiFieldClass(field, 'gui-fieldset')}`;
      const header = document.createElement('div');
      header.className = 'card-head';
      const title = document.createElement('h3');
      title.textContent = guiFieldLabel(field, 'Einstellungen');
      header.appendChild(title);
      section.appendChild(header);

      const grid = document.createElement('div');
      grid.className = 'form-grid gui-field-grid';
      for (const child of field.fields || []) {
        const childElement = createSourceField(child, model);
        if (childElement) grid.appendChild(childElement);
      }
      section.appendChild(grid);

      if (field.help) {
        const help = document.createElement('small');
        help.className = 'help';
        help.textContent = field.help;
        section.appendChild(help);
      }
      return section;
    }

    if (!field.name) return null;

    const label = document.createElement('label');
    label.className = guiFieldClass(field);
    if (field.type === 'checkbox') {
      label.classList.add('checkbox-row');
    }

    if (field.disabled_when_driver && model?.driver === field.disabled_when_driver) {
      field = { ...field, disabled: true, value: false };
    }

    const caption = document.createElement('span');
    caption.className = 'field-label';
    caption.textContent = guiFieldLabel(field, field.name);
    appendRequiredMarker(caption, field);

    let input;
    if (field.type === 'select') {
      input = document.createElement('select');
      input.name = field.name;
      if (field.required) input.required = true;

      const options = Array.isArray(field.options) ? field.options : [];
      if (!field.value && field.required) {
        const placeholder = document.createElement('option');
        placeholder.value = '';
        placeholder.disabled = true;
        placeholder.selected = true;
        placeholder.textContent = 'Bitte auswählen …';
        input.appendChild(placeholder);
      }

      for (const item of options) {
        const option = document.createElement('option');
        const value = String(item.value ?? '');
        option.value = value;
        option.textContent = String(item.label ?? value);
        option.selected = value === String(field.value ?? '');
        input.appendChild(option);
      }

    } else if (field.type === 'checkbox') {
      input = document.createElement('input');
      input.type = 'checkbox';
      input.name = field.name;
      input.checked = Boolean(field.value);
    } else {
      input = document.createElement('input');
      input.type = field.type || 'text';
      input.name = field.name;
      if (field.value !== null && field.value !== undefined) input.value = field.value;
      if (field.step !== null && field.step !== undefined) input.step = String(field.step);
      if (field.min !== null && field.min !== undefined) input.min = String(field.min);
      if (field.max !== null && field.max !== undefined) input.max = String(field.max);
      if (field.required) input.required = true;
    }


    if (field.refresh_on_change) input.dataset.sourceRefreshConfig = '1';
    if (field.action_on_change) {
      input.dataset.sourceActionOnChange = String(field.action_on_change);
      input.dataset.sourceActionOnChangeBusyText = field.action_on_change_busy_text || 'Wird angewendet …';
      input.dataset.sourceActionOnChangeEmpty = field.action_on_change_empty || '';
      input.dataset.sourceId = model?.id || model?.role || '';
    }
    if (field.disabled) input.disabled = true;

    if (field.type === 'checkbox') {
      label.appendChild(input);
      label.appendChild(caption);
    } else {
      label.appendChild(caption);
      label.appendChild(input);
    }

    if (field.help) {
      const help = document.createElement('small');
      help.className = 'help';
      help.textContent = field.help;
      label.appendChild(help);
    }

    return label;
  }

  function sourceFieldKey(field, fallback = '') {
    return String(field?.name || field?.key || field?.id || field?.label || fallback || '').toLowerCase();
  }

  function createSourceBadge(label, value, field = null, index = 0) {
    const badge = document.createElement('span');
    badge.className = 'badge source-header-badge';
    badge.dataset.sourceHeaderField = sourceFieldKey(field, index);
    const labelEl = document.createElement('span');
    labelEl.className = 'source-header-badge-label';
    labelEl.textContent = label;
    const valueEl = document.createElement('strong');
    valueEl.dataset.sourceHeaderValue = '';
    valueEl.textContent = value === null || value === undefined || value === '' ? '—' : String(value);
    badge.appendChild(labelEl);
    badge.appendChild(valueEl);
    return badge;
  }

  function createSourceHeaderBadges(model) {
    const row = document.createElement('div');
    row.className = 'badge-row compact source-header-badges';

    const fields = Array.isArray(model?.header_fields) ? model.header_fields : [];
    fields.forEach((field, index) => {
      row.appendChild(createSourceBadge(field.label || '', formatSourceValue(field), field, index));
    });

    return row;
  }

  function createSourceDetails(model) {
    const groups = Array.isArray(model?.detail_groups) ? model.detail_groups.filter((group) => Array.isArray(group?.fields) && group.fields.length) : [];
    if (!groups.length) return null;

    const section = document.createElement('section');
    section.className = 'details-section source-details-section';

    const title = document.createElement('strong');
    title.textContent = 'Details';
    section.appendChild(title);

    const grid = document.createElement('div');
    grid.className = 'details-grid source-details-grid';
    grid.style.marginTop = '.75rem';

    for (const group of groups) {
      for (const field of group.fields || []) {
        const item = document.createElement('div');
        item.className = 'details-item source-details-item';
        item.dataset.sourceDetailField = sourceFieldKey(field);
        const label = document.createElement('span');
        label.className = 'details-item-label source-details-label';
        label.textContent = field.label || '';
        const value = document.createElement('span');
        value.className = 'source-details-value';
        value.dataset.sourceDetailValue = '';
        value.textContent = formatSourceValue(field);
        item.appendChild(label);
        item.appendChild(value);
        grid.appendChild(item);
      }
    }

    section.appendChild(grid);
    return section;
  }

  function createSourceActions(model) {
    const actions = Array.isArray(model?.actions) ? model.actions : [];
    if (!actions.length) return null;

    const wrapper = document.createElement('div');
    wrapper.className = 'actions top-gap source-driver-actions';

    for (const action of actions) {
      if (!action?.id) continue;
      const button = document.createElement('button');
      button.type = 'button';
      button.className = action.style === 'danger' ? 'btn danger' : 'btn secondary';
      button.dataset.sourceAction = action.id;
      button.dataset.sourceId = model.id || model.role || '';
      button.textContent = action.label || action.id;
      if (action.help) button.title = action.help;
      wrapper.appendChild(button);
    }

    return wrapper;
  }


  function setSourceCardExpanded(toggle, expanded) {
    if (!toggle) return;
    const card = toggle.closest('[data-source-card]');
    const panelId = toggle.dataset.sourceCardToggle;
    const panel = panelId ? document.getElementById(panelId) : card?.querySelector('.measurement-config-panel');
    if (!card || !panel) return;

    const nextExpanded = typeof expanded === 'boolean' ? expanded : card.dataset.sourceExpanded !== 'true';
    card.dataset.sourceExpanded = nextExpanded ? 'true' : 'false';
    toggle.setAttribute('aria-expanded', nextExpanded ? 'true' : 'false');
    panel.hidden = !nextExpanded;
  }

  function bindPanelToggles() {
    if (window.pv2hashSourceToggleBound) return;
    window.pv2hashSourceToggleBound = true;

    document.addEventListener('click', (event) => {
      const toggle = event.target.closest('[data-source-card-toggle]');
      if (!toggle) return;
      event.preventDefault();
      setSourceCardExpanded(toggle);
    });

    document.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      const toggle = event.target.closest('[data-source-card-toggle]');
      if (!toggle) return;
      event.preventDefault();
      setSourceCardExpanded(toggle);
    });
  }

  function createSourceCard(model, options = {}) {
    const card = document.createElement('article');
    card.className = 'card measurement-summary-card source-model-card';
    card.dataset.sourceModelId = model.id || '';
    card.dataset.sourceCard = '';

    const panelId = `source-config-panel-${model.id || model.role || Math.random().toString(16).slice(2)}`;
    const expanded = Boolean(options.openIds?.has(model.id || model.role || panelId));
    card.dataset.sourceExpanded = expanded ? 'true' : 'false';

    const head = document.createElement('div');
    head.className = 'card-head source-card-head';
    head.dataset.sourceCardToggle = panelId;
    head.tabIndex = 0;
    head.setAttribute('role', 'button');
    head.setAttribute('aria-controls', panelId);
    head.setAttribute('aria-expanded', expanded ? 'true' : 'false');

    const titleBlock = document.createElement('div');
    titleBlock.className = 'source-card-title-block';
    const title = document.createElement('h2');
    title.textContent = model.title || model.role || 'Source';
    const subtitle = document.createElement('p');
    subtitle.className = 'card-subtitle';
    subtitle.textContent = model.driver_label || model.driver || '';
    titleBlock.appendChild(title);
    titleBlock.appendChild(subtitle);
    titleBlock.appendChild(createSourceHeaderBadges(model));

    const affordance = document.createElement('span');
    affordance.className = 'source-card-affordance';
    affordance.setAttribute('aria-hidden', 'true');
    affordance.textContent = '▾';

    head.appendChild(titleBlock);
    head.appendChild(affordance);
    card.appendChild(head);

    const panel = document.createElement('div');
    panel.className = 'measurement-config-panel top-gap';
    panel.id = panelId;
    panel.hidden = !expanded;

    const details = createSourceDetails(model);
    if (details) panel.appendChild(details);

    const basic = document.createElement('section');
    basic.className = 'card type-subcard';
    const basicHead = document.createElement('div');
    basicHead.className = 'card-head';
    const basicTitleBlock = document.createElement('div');
    const basicTitle = document.createElement('h2');
    basicTitle.textContent = 'Konfiguration';
    const basicSubtitle = document.createElement('p');
    basicSubtitle.className = 'card-subtitle';
    basicSubtitle.textContent = 'Diese Felder werden vom Source-Modell geliefert.';
    basicTitleBlock.appendChild(basicTitle);
    basicTitleBlock.appendChild(basicSubtitle);
    basicHead.appendChild(basicTitleBlock);
    basic.appendChild(basicHead);

    const grid = document.createElement('div');
    grid.className = 'form-grid gui-field-grid';

    if (model.driver_field) {
      const field = createSourceField({ ...model.driver_field, refresh_on_change: true }, model);
      if (field) grid.appendChild(field);
    }

    for (const field of model.config_fields || []) {
      const element = createSourceField(field, model);
      if (element) grid.appendChild(element);
    }

    basic.appendChild(grid);
    const driverActions = createSourceActions(model);
    if (driverActions) basic.appendChild(driverActions);
    panel.appendChild(basic);

    const actionsRow = document.createElement('div');
    actionsRow.className = 'actions top-gap split';

    const spacer = document.createElement('span');
    spacer.setAttribute('aria-hidden', 'true');

    const submit = document.createElement('button');
    submit.className = 'btn';
    submit.type = 'submit';
    submit.textContent = 'Speichern';

    actionsRow.appendChild(spacer);
    actionsRow.appendChild(submit);
    panel.appendChild(actionsRow);

    card.appendChild(panel);
    return card;
  }

  function renderSourcesGuiModels(models) {
    const container = document.querySelector('[data-sources-model-container]');
    if (!container) return;

    const openIds = new Set();
    container.querySelectorAll('[data-source-card][data-source-expanded="true"]').forEach((card) => {
      if (card.dataset.sourceModelId) openIds.add(card.dataset.sourceModelId);
    });

    container.innerHTML = '';
    for (const model of models || []) {
      container.appendChild(createSourceCard(model, { openIds }));
    }

    bindPanelToggles();
  }

  function updateSourceRuntimeViews(models) {
    const container = document.querySelector('[data-sources-model-container]');
    if (!container) return false;

    let updated = false;
    for (const model of models || []) {
      const modelId = String(model.id || model.role || '');
      if (!modelId) continue;
      const card = container.querySelector(`[data-source-card][data-source-model-id="${cssEscape(modelId)}"]`);
      if (!card) continue;

      const subtitle = card.querySelector('.source-card-title-block .card-subtitle');
      if (subtitle) subtitle.textContent = model.driver_label || model.driver || '';

      const headerFields = Array.isArray(model.header_fields) ? model.header_fields : [];
      headerFields.forEach((field, index) => {
        const key = sourceFieldKey(field, index);
        const value = card.querySelector(`[data-source-header-field="${cssEscape(key)}"] [data-source-header-value]`);
        if (value) value.textContent = formatSourceValue(field);
      });

      const detailGroups = Array.isArray(model.detail_groups) ? model.detail_groups : [];
      for (const group of detailGroups) {
        for (const field of group.fields || []) {
          const key = sourceFieldKey(field);
          const value = card.querySelector(`[data-source-detail-field="${cssEscape(key)}"] [data-source-detail-value]`);
          if (value) value.textContent = formatSourceValue(field);
        }
      }
      updated = true;
    }
    return updated;
  }

  function updateSourcesGuiModels(data, options = {}) {
    const models = sourceModelsFromPayload(data);
    window.pv2hashSourcesGuiModels = models;
    displaySourceModelWarnings(models);

    if (options.forceRender) {
      renderSourcesGuiModels(models);
      return;
    }

    if (!updateSourceRuntimeViews(models)) {
      renderSourcesGuiModels(models);
    }
  }

  function updateSourcesSummary(data, options = {}) {
    if (!data) return;
    updateSourcesGuiModels(data, options);
  }

  let sourcesPreviewRunning = false;

  async function previewSourcesConfig(form) {
    if (!form || sourcesPreviewRunning) return;
    sourcesPreviewRunning = true;
    try {
      const data = await postForm('/api/sources/gui/preview', form);
      updateSourcesSummary(data, { forceRender: true });
    } catch (error) {
      window.showToast('error', error.message || 'Source-Profil konnte nicht aktualisiert werden.');
    } finally {
      sourcesPreviewRunning = false;
    }
  }

  async function runSourceAction(form, element) {
    if (!form || !element || form.dataset.busy === '1') return;

    const actionId = element.dataset.sourceAction || element.dataset.sourceActionOnChange || '';
    const sourceId = element.dataset.sourceId || '';
    if (!actionId || !sourceId) return;

    if (element instanceof HTMLSelectElement && !element.value && element.dataset.sourceActionOnChangeEmpty !== 'run') {
      if (element.dataset.sourceActionOnChangeEmpty === 'preview') {
        await previewSourcesConfig(form);
      }
      return;
    }

    const payload = new FormData(form);
    payload.set('source_id', sourceId);
    payload.set('action_id', actionId);

    const busyText = element.dataset.sourceActionOnChangeBusyText || (element.dataset.sourceActionOnChange ? 'Wird angewendet …' : 'Suche …');
    const restore = element instanceof HTMLButtonElement ? setButtonBusy(element, busyText) : (() => {});
    if (!(element instanceof HTMLButtonElement)) {
      element.disabled = true;
      element.setAttribute('aria-busy', 'true');
    }
    setFormBusy(form, true);
    try {
      const response = await fetch('/api/sources/action', {
        method: 'POST',
        body: payload,
        headers: { 'Accept': 'application/json' },
      });
      const data = await readJsonResponse(response, 'Source-Aktion fehlgeschlagen.');
      updateSourcesSummary(data, { forceRender: true });
      window.showToast(data.status === 'error' ? 'error' : 'success', data.message || 'Aktion abgeschlossen.');
    } catch (error) {
      window.showToast('error', error.message || 'Source-Aktion fehlgeschlagen.');
    } finally {
      setFormBusy(form, false);
      if (!(element instanceof HTMLButtonElement)) {
        element.disabled = false;
        element.removeAttribute('aria-busy');
      }
      restore();
    }
  }

  async function submitSourcesConfig(form, submitter) {
    if (!form || form.dataset.busy === '1') return;

    const restore = setButtonBusy(submitter || form.querySelector('[type="submit"]'), 'Speichert …');
    setFormBusy(form, true);

    try {
      const data = await postForm('/api/sources/config', form);
      updateSourcesSummary(data, { forceRender: true });
      window.showToast('success', data.message || 'Messungen gespeichert.');
    } catch (error) {
      window.showToast('error', error.message || 'Messungen konnten nicht gespeichert werden.');
    } finally {
      setFormBusy(form, false);
      restore();
    }
  }

  let sourcesRefreshTimer = null;
  let sourcesRefreshRunning = false;
  let sourcesRefreshFailureCount = 0;

  async function refreshSourcesLiveData(options = {}) {
    const root = document.querySelector('[data-sources-live-root]');
    const form = document.querySelector('[data-sources-config-form]');
    if (!root || document.hidden || sourcesRefreshRunning) return;
    if (form && form.dataset.busy === '1') return;

    sourcesRefreshRunning = true;
    try {
      const response = await fetch('/api/sources/status', { headers: { 'Accept': 'application/json' }, cache: 'no-store' });
      const data = await readJsonResponse(response, 'Messdaten konnten nicht aktualisiert werden.');
      if (document.hidden) return;
      sourcesRefreshFailureCount = 0;
      updateSourcesSummary(data, options);
    } catch (error) {
      sourcesRefreshFailureCount += 1;
      if (sourcesRefreshFailureCount === 1 || sourcesRefreshFailureCount % 10 === 0) {
        console.debug('PV2Hash sources refresh failed:', error);
      }
    } finally {
      sourcesRefreshRunning = false;
    }
  }

  function startSourcesLiveRefresh() {
    const root = document.querySelector('[data-sources-live-root]');
    if (!root || document.hidden) return;
    stopSourcesLiveRefresh();
    const seconds = Math.max(2, Number(root.dataset.refreshSeconds || 5));
    sourcesRefreshTimer = window.setInterval(refreshSourcesLiveData, seconds * 1000);
  }

  function stopSourcesLiveRefresh() {
    if (sourcesRefreshTimer) {
      window.clearInterval(sourcesRefreshTimer);
      sourcesRefreshTimer = null;
    }
  }

  function setupSourcesPage() {
    const root = document.querySelector('[data-sources-live-root]');
    if (!root) return;

    const form = document.querySelector('[data-sources-config-form]');
    if (form) {
      form.addEventListener('submit', (event) => {
        event.preventDefault();
        submitSourcesConfig(form, event.submitter);
      });

      form.addEventListener('change', (event) => {
        const target = event.target;
        if (!(target instanceof HTMLSelectElement)) return;
        if (target.dataset.sourceActionOnChange) {
          runSourceAction(form, target);
          return;
        }
        if (target.dataset.sourceRefreshConfig !== '1') return;
        previewSourcesConfig(form);
      });

      form.addEventListener('click', (event) => {
        const button = event.target instanceof Element ? event.target.closest('[data-source-action]') : null;
        if (!button) return;
        event.preventDefault();
        runSourceAction(form, button);
      });
    }

    const params = new URLSearchParams(window.location.search);
    if (params.get('saved') === '1') {
      window.showToast('success', 'Messungen gespeichert.');
      window.history.replaceState({}, document.title, window.location.pathname);
    }

    document.addEventListener('visibilitychange', () => {
      if (document.hidden) {
        stopSourcesLiveRefresh();
      } else {
        refreshSourcesLiveData();
        startSourcesLiveRefresh();
      }
    });

    refreshSourcesLiveData({ forceRender: true });
    startSourcesLiveRefresh();
  }


  function settingsModelFromPayload(data) {
    return data?.model && typeof data.model === 'object' ? data.model : { sections: [] };
  }

  function createSettingsField(field) {
    // Settings fields use the same global GUI field model as Sources/Miners.
    return createSourceField(field, null);
  }

  function createSettingsSection(section) {
    const card = document.createElement('article');
    card.className = 'card';
    card.dataset.settingsSection = section?.id || '';

    const head = document.createElement('div');
    head.className = 'card-head';
    const titleBlock = document.createElement('div');
    const title = document.createElement('h2');
    title.textContent = section?.title || 'Einstellungen';
    titleBlock.appendChild(title);
    if (section?.subtitle || section?.description) {
      const subtitle = document.createElement('p');
      subtitle.className = 'card-subtitle';
      subtitle.textContent = section.subtitle || section.description;
      titleBlock.appendChild(subtitle);
    }
    head.appendChild(titleBlock);
    card.appendChild(head);

    const grid = document.createElement('div');
    grid.className = 'form-grid gui-field-grid';
    for (const field of section?.fields || []) {
      const element = createSettingsField(field);
      if (element) grid.appendChild(element);
    }
    card.appendChild(grid);
    return card;
  }

  function renderSettingsModel(model) {
    const container = document.querySelector('[data-settings-model-container]');
    if (!container) return;
    container.innerHTML = '';
    for (const section of model?.sections || []) {
      container.appendChild(createSettingsSection(section));
    }
  }

  async function loadSettingsModel() {
    const root = document.querySelector('[data-settings-root]');
    if (!root) return;
    try {
      const response = await fetch('/api/settings/model', { headers: { 'Accept': 'application/json' }, cache: 'no-store' });
      const data = await readJsonResponse(response, 'Einstellungen konnten nicht geladen werden.');
      renderSettingsModel(settingsModelFromPayload(data));
    } catch (error) {
      window.showToast('error', error.message || 'Einstellungen konnten nicht geladen werden.');
    }
  }

  function collectSettingsValues(form) {
    const values = {};
    for (const element of form.querySelectorAll('input[name], select[name], textarea[name]')) {
      if (element.disabled) continue;
      if (element instanceof HTMLInputElement && element.type === 'checkbox') {
        values[element.name] = element.checked;
      } else {
        values[element.name] = element.value;
      }
    }
    return values;
  }

  async function submitSettingsForm(form, submitter) {
    if (!form || form.dataset.busy === '1') return;
    const restore = setButtonBusy(submitter || form.querySelector('[type="submit"]'), 'Speichert …');
    setFormBusy(form, true);
    try {
      const data = await postJson('/api/settings/config', collectSettingsValues(form));
      renderSettingsModel(settingsModelFromPayload(data));
      window.showToast('success', data.message || 'Einstellungen gespeichert.');
      const navSubtitle = document.querySelector('[data-nav-subtitle]');
      if (navSubtitle && data.instance_name) navSubtitle.textContent = data.instance_name;
    } catch (error) {
      window.showToast('error', error.message || 'Einstellungen konnten nicht gespeichert werden.');
    } finally {
      setFormBusy(form, false);
      restore();
    }
  }

  function setupSettingsPage() {
    const root = document.querySelector('[data-settings-root]');
    if (!root) return;
    const form = document.querySelector('[data-settings-form]');
    if (form) {
      form.addEventListener('submit', (event) => {
        event.preventDefault();
        submitSettingsForm(form, event.submitter || form.querySelector('[data-settings-save]'));
      });

      const saveButton = form.querySelector('[data-settings-save]');
      if (saveButton) {
        saveButton.addEventListener('click', (event) => {
          event.preventDefault();
          submitSettingsForm(form, saveButton);
        });
      }
    }

    loadSettingsModel();
  }


  let versionStatusTimer = null;
  let versionStatusRunning = false;
  let versionStatusFailureCount = 0;

  window.applyPv2HashVersionStatus = function applyPv2HashVersionStatus(data) {
    if (!data) return;
    const link = document.querySelector('[data-versionstatus]');
    if (!link) return;

    const updateAvailable = Boolean(data.update_available || data.status === 'update_available');
    const versionFull = data.version_full || data.local_version_full || '';
    const text = link.querySelector('[data-versionstatus-text]');
    const indicator = link.querySelector('[data-versionstatus-indicator]');
    const updateLabel = link.querySelector('[data-versionstatus-update-label]');

    if (text) {
      text.textContent = versionFull ? `Version ${versionFull}` : 'Version unbekannt';
    }

    if (updateLabel) {
      updateLabel.textContent = 'Update verfügbar';
      updateLabel.hidden = !updateAvailable;
    }

    link.classList.toggle('brand-version-alert', updateAvailable);
    link.dataset.versionstatusState = data.update_status || data.status || 'unknown';
    link.href = data.href || '/system';
    link.title = data.title || (updateAvailable ? 'Update verfügbar – zur Systemseite wechseln' : 'Zur Systemseite wechseln');

    if (indicator) {
      indicator.hidden = !updateAvailable;
    }
  };

  async function refreshVersionStatus() {
    const link = document.querySelector('[data-versionstatus]');
    if (!link || document.hidden || versionStatusRunning) return;

    versionStatusRunning = true;
    try {
      const response = await fetch('/api/ui/versionstatus', { headers: { 'Accept': 'application/json' }, cache: 'no-store' });
      const data = await readJsonResponse(response, 'Versionsstatus konnte nicht aktualisiert werden.');
      if (document.hidden) return;
      versionStatusFailureCount = 0;
      window.applyPv2HashVersionStatus(data);
    } catch (error) {
      versionStatusFailureCount += 1;
      if (versionStatusFailureCount === 1 || versionStatusFailureCount % 10 === 0) {
        console.debug('PV2Hash versionstatus refresh failed:', error);
      }
    } finally {
      versionStatusRunning = false;
    }
  }

  function startVersionStatusRefresh() {
    const link = document.querySelector('[data-versionstatus]');
    if (!link || document.hidden) return;
    stopVersionStatusRefresh();
    versionStatusTimer = window.setInterval(refreshVersionStatus, 60 * 1000);
  }

  function stopVersionStatusRefresh() {
    if (versionStatusTimer) {
      window.clearInterval(versionStatusTimer);
      versionStatusTimer = null;
    }
  }


  let systemModelTimer = null;
  let systemLogTimer = null;
  let systemModelLoading = false;
  let systemLogLines = [];
  let systemAutoScroll = true;
  let systemLogsVisible = true;
  let systemLastUpdateStatus = null;
  let systemUpdateOverlayRedirectTimer = null;
  const systemUpdateOverlaySessionKey = 'pv2hash.systemUpdateOverlayStarted';

  function systemEscapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text ?? '';
    return div.innerHTML;
  }

  function systemFormatDate(value) {
    if (!value) return '—';
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return String(value);
    return parsed.toLocaleString('de-DE');
  }

  function systemSetProgress(fill, percentElement, value) {
    const percent = Number(value);
    if (!Number.isFinite(percent)) {
      if (fill) fill.style.width = '0%';
      if (percentElement) percentElement.textContent = '—';
      return false;
    }

    const bounded = Math.max(0, Math.min(100, Math.round(percent)));
    if (fill) {
      fill.style.width = `${bounded}%`;
      const bar = fill.closest('.update-progress-bar');
      if (bar) bar.setAttribute('aria-valuenow', String(bounded));
    }
    if (percentElement) percentElement.textContent = `${bounded}%`;
    return true;
  }

  function systemUpdateBadge(status) {
    const map = {
      disabled: { label: 'Deaktiviert', className: 'status-neutral' },
      checking: { label: 'Prüft …', className: 'status-info' },
      update_available: { label: 'Update verfügbar', className: 'status-warn' },
      up_to_date: { label: 'Aktuell', className: 'status-good' },
      ahead_of_release: { label: 'Lokaler Stand neuer', className: 'status-info' },
      error: { label: 'Fehler', className: 'status-bad' },
    };
    return map[status] || { label: status || 'Unbekannt', className: 'status-neutral' };
  }

  function systemUpdateMessage(status, data) {
    if (status === 'disabled') return 'Update-Prüfung ist in der Konfiguration deaktiviert.';
    if (status === 'checking') return 'GitHub Releases werden gerade geprüft …';
    if (status === 'update_available') return `Es ist ein neueres Release verfügbar: ${data.release_version_full || data.release_tag || '—'}.`;
    if (status === 'up_to_date') return 'Die lokale Installation entspricht dem aktuellen Latest Release.';
    if (status === 'ahead_of_release') return 'Der lokale Stand ist neuer als das aktuelle Latest Release auf GitHub.';
    if (status === 'error') return data.error || 'Die Update-Prüfung ist fehlgeschlagen.';
    return 'Kein Update-Status vorhanden.';
  }

  function createSystemCard(title, subtitle = '') {
    const card = document.createElement('article');
    card.className = 'card';
    const head = document.createElement('div');
    head.className = 'card-head';
    const titleBlock = document.createElement('div');
    const h2 = document.createElement('h2');
    h2.textContent = title || 'System';
    titleBlock.appendChild(h2);
    if (subtitle) {
      const p = document.createElement('p');
      p.className = 'card-subtitle';
      p.textContent = subtitle;
      titleBlock.appendChild(p);
    }
    head.appendChild(titleBlock);
    card.appendChild(head);
    return card;
  }

  function createSystemKv(rows) {
    const kv = document.createElement('div');
    kv.className = 'kv';
    for (const row of rows || []) {
      const item = document.createElement('div');
      item.className = 'kv-row';
      const label = document.createElement('span');
      label.textContent = row.label || '';
      const value = document.createElement('strong');
      value.textContent = row.value ?? '—';
      item.appendChild(label);
      item.appendChild(value);
      kv.appendChild(item);
    }
    return kv;
  }

  function renderSystemBackupCard(cardModel) {
    const card = createSystemCard(cardModel.title, cardModel.description);
    const actions = document.createElement('div');
    actions.className = 'actions start wrap top-gap';

    const exportLink = document.createElement('a');
    exportLink.className = 'btn btn-secondary';
    exportLink.href = cardModel.export_url || '/system/config/export';
    exportLink.textContent = 'Konfiguration herunterladen';
    actions.appendChild(exportLink);
    card.appendChild(actions);

    const form = document.createElement('form');
    form.className = 'top-gap';
    form.dataset.systemConfigImportForm = '1';

    const grid = document.createElement('div');
    grid.className = 'form-grid gui-field-grid';
    const label = document.createElement('label');
    label.className = 'gui-field gui-field-full is-required';
    const span = document.createElement('span');
    span.className = 'field-label';
    span.textContent = 'Konfigurationsdatei';
    appendRequiredMarker(span, { required: true });
    const input = document.createElement('input');
    input.type = 'file';
    input.name = 'config_file';
    input.accept = 'application/json,.json';
    input.required = true;
    const help = document.createElement('small');
    help.className = 'help';
    help.textContent = 'Erwartet eine PV2Hash-Konfiguration im JSON-Format.';
    label.appendChild(span);
    label.appendChild(input);
    label.appendChild(help);
    grid.appendChild(label);
    form.appendChild(grid);

    const submitActions = document.createElement('div');
    submitActions.className = 'actions end top-gap';
    const submit = document.createElement('button');
    submit.className = 'btn btn-warning';
    submit.type = 'submit';
    submit.dataset.submitButton = '1';
    submit.textContent = 'Konfiguration importieren';
    submitActions.appendChild(submit);
    form.appendChild(submitActions);
    card.appendChild(form);

    return card;
  }

  function renderSystemDetailsCard(cardModel) {
    const card = createSystemCard(cardModel.title, cardModel.description || '');
    card.appendChild(createSystemKv(cardModel.rows || []));

    if (Array.isArray(cardModel.actions) && cardModel.actions.length) {
      const actions = document.createElement('div');
      actions.className = 'actions start wrap top-gap';
      for (const action of cardModel.actions) {
        const button = document.createElement('button');
        button.className = action.style === 'warning' ? 'btn btn-warning' : 'btn';
        button.type = 'button';
        button.dataset.systemAction = action.id || '';
        button.textContent = action.label || action.id || 'Aktion';
        actions.appendChild(button);
      }
      card.appendChild(actions);
    }

    return card;
  }

  function renderSystemUpdateCard(cardModel) {
    const update = cardModel.model || {};
    const updateStatus = update.update_status || {};
    const runner = update.runner_status || {};
    const details = update.release_details || {};
    systemLastUpdateStatus = updateStatus;

    const card = createSystemCard(cardModel.title || 'Updates');
    const head = card.querySelector('.card-head');

    const actions = document.createElement('div');
    actions.className = 'system-update-head-actions';
    const badgeInfo = systemUpdateBadge(updateStatus.status);
    const badge = document.createElement('span');
    badge.className = `status-pill ${badgeInfo.className}`;
    badge.textContent = badgeInfo.label;
    actions.appendChild(badge);

    const checkButton = document.createElement('button');
    checkButton.className = 'btn btn-secondary';
    checkButton.type = 'button';
    checkButton.dataset.systemUpdateCheck = '1';
    checkButton.textContent = 'Jetzt auf Updates prüfen';
    actions.appendChild(checkButton);
    head.appendChild(actions);

    const kvRows = [
      { label: 'Lokale Version', value: updateStatus.local_version_full || '—' },
      { label: 'Release-Version', value: updateStatus.release_version_full || '—' },
      { label: 'Release-Tag', value: updateStatus.release_tag || '—' },
      { label: 'Geprüft am', value: systemFormatDate(updateStatus.checked_at) },
      { label: 'Veröffentlicht am', value: systemFormatDate(updateStatus.release_published_at) },
    ];
    card.appendChild(createSystemKv(kvRows));

    const message = document.createElement('div');
    message.className = updateStatus.status === 'error' ? 'update-error top-gap' : 'update-message top-gap';
    message.textContent = systemUpdateMessage(updateStatus.status, updateStatus);
    card.appendChild(message);

    if (runner && runner.progress_percent !== null && runner.progress_percent !== undefined) {
      const progressWrap = document.createElement('div');
      progressWrap.className = 'update-progress-inline top-gap';
      progressWrap.innerHTML = `
        <div class="update-progress-inline-head">
          <span>Fortschritt</span>
          <strong data-system-update-progress-percent>—</strong>
        </div>
        <div class="update-progress-bar" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0">
          <div class="update-progress-fill" data-system-update-progress-fill></div>
        </div>
      `;
      systemSetProgress(
        progressWrap.querySelector('[data-system-update-progress-fill]'),
        progressWrap.querySelector('[data-system-update-progress-percent]'),
        runner.progress_percent,
      );
      card.appendChild(progressWrap);
    }

    if (updateStatus.release_url) {
      const links = document.createElement('div');
      links.className = 'update-links';
      const link = document.createElement('a');
      link.className = 'link-muted';
      link.href = updateStatus.release_url;
      link.target = '_blank';
      link.rel = 'noopener';
      link.textContent = 'Release auf GitHub öffnen';
      links.appendChild(link);
      card.appendChild(links);
    }

    const hasDetails = !!(details.name || details.body || details.asset_name || details.asset_size_text);
    if (hasDetails) {
      const detailBlock = document.createElement('details');
      detailBlock.className = 'update-release-details';
      const summary = document.createElement('summary');
      summary.textContent = 'Release-Details anzeigen';
      detailBlock.appendChild(summary);
      detailBlock.appendChild(createSystemKv([
        { label: 'Release-Name', value: details.name || '—' },
        { label: 'Paket', value: details.asset_name || '—' },
        { label: 'Paketgröße', value: details.asset_size_text || '—' },
      ]));
      const notes = document.createElement('pre');
      notes.className = 'update-release-notes';
      notes.textContent = details.body || 'Keine Release Notes vorhanden.';
      detailBlock.appendChild(notes);
      card.appendChild(detailBlock);
    }

    if (updateStatus.release_tag) {
      const commandWrap = document.createElement('div');
      commandWrap.className = 'update-command-wrap';
      const label = document.createElement('div');
      label.className = 'update-command-label';
      label.textContent = 'Installationsbefehl für dieses Release';
      const command = document.createElement('code');
      command.className = 'update-command';
      command.textContent = `curl -fsSL https://raw.githubusercontent.com/phlupp/pv2hash/main/scripts/install_release.sh | sudo env TAG=${updateStatus.release_tag} bash`;
      commandWrap.appendChild(label);
      commandWrap.appendChild(command);
      card.appendChild(commandWrap);
    }

    const footer = document.createElement('div');
    footer.className = 'actions end wrap top-gap system-update-footer';
    const install = document.createElement('button');
    install.className = 'btn btn-warning';
    install.type = 'button';
    install.dataset.systemUpdateStart = '1';

    const updateAvailable = updateStatus.status === 'update_available';
    const running = !!runner.running;
    if (running) {
      install.disabled = true;
      install.textContent = 'Update läuft …';
    } else if (runner.can_start && updateAvailable) {
      install.disabled = false;
      install.textContent = `Jetzt auf ${updateStatus.release_version_full || updateStatus.release_tag || 'Latest'} aktualisieren`;
    } else {
      install.disabled = true;
      install.textContent = 'Update starten';
    }
    footer.appendChild(install);
    card.appendChild(footer);

    if (window.applyPv2HashVersionStatus) {
      window.applyPv2HashVersionStatus({
        status: updateStatus.status,
        update_status: updateStatus.status,
        version_full: updateStatus.local_version_full,
        update_available: updateAvailable,
        release_version_full: updateStatus.release_version_full,
        release_tag: updateStatus.release_tag,
        href: '/system',
      });
    }

    renderSystemUpdateOverlay(runner);

    return card;
  }

  function renderSystemLogsCard(cardModel) {
    const card = createSystemCard(cardModel.title, cardModel.description || '');
    card.classList.add('system-logs-card');

    const toolbar = document.createElement('div');
    toolbar.className = 'console-toolbar';
    toolbar.innerHTML = `
      <div class="console-toolbar-controls">
        <div class="console-control-row console-control-row-primary">
          <label class="console-filter-label" for="systemLogLevel">Log-Level</label>
          <select id="systemLogLevel" data-system-log-level></select>
          <button class="btn btn-secondary" type="button" data-system-log-level-save>Log-Level setzen</button>
          <a class="btn btn-info" href="${cardModel.download_url || '/api/logs/download'}">Log herunterladen</a>
        </div>
        <div class="console-control-row console-control-row-filter">
          <label class="console-filter-label" for="systemLogFilter">Filter</label>
          <input id="systemLogFilter" class="console-filter-input" type="text" placeholder="Suche in Logzeilen …" data-system-log-filter>
          <button class="btn btn-secondary" type="button" data-system-log-filter-clear>Filter löschen</button>
        </div>
        <div class="console-control-row console-control-row-actions">
          <button class="btn btn-secondary" type="button" data-system-log-scroll-toggle>${systemAutoScroll ? 'STOPPEN SCROLLING' : 'STARTEN SCROLLING'}</button>
          <button class="btn btn-secondary" type="button" data-system-log-visibility-toggle>${systemLogsVisible ? 'AUSBLENDEN LOGS' : 'EINBLENDEN LOGS'}</button>
        </div>
      </div>
    `;
    card.appendChild(toolbar);

    const select = toolbar.querySelector('[data-system-log-level]');
    for (const item of cardModel.allowed_log_levels || ['INFO', 'DEBUG']) {
      const option = document.createElement('option');
      option.value = item;
      option.textContent = item;
      option.selected = item === cardModel.log_level;
      select.appendChild(option);
    }

    const wrap = document.createElement('div');
    wrap.className = 'console-wrap';
    wrap.dataset.systemConsoleWrap = '1';
    wrap.style.display = systemLogsVisible ? 'block' : 'none';
    const output = document.createElement('div');
    output.className = 'console-output';
    output.dataset.systemLogConsole = '1';
    output.innerHTML = '<div class="log-row"><div class="log-message-full">Lade Logs...</div></div>';
    wrap.appendChild(output);
    card.appendChild(wrap);

    return card;
  }

  function renderSystemModel(model) {
    const container = document.querySelector('[data-system-model-container]');
    if (!container) return;

    const previousLogFilter = document.querySelector('[data-system-log-filter]')?.value || '';
    const previousReleaseDetailsOpen = Boolean(document.querySelector('.update-release-details')?.open);
    container.innerHTML = '';

    const topGrid = document.createElement('section');
    topGrid.className = 'grid grid-2';
    const midGrid = document.createElement('section');
    midGrid.className = 'grid grid-2 top-gap';
    const standaloneCards = [];

    for (const card of model?.cards || []) {
      let element;
      if (card.type === 'backup') element = renderSystemBackupCard(card);
      else if (card.type === 'update') element = renderSystemUpdateCard(card);
      else if (card.type === 'logs') element = renderSystemLogsCard(card);
      else element = renderSystemDetailsCard(card);

      if (card.id === 'backup' || card.id === 'update') {
        topGrid.appendChild(element);
      } else if (card.id === 'instance' || card.id === 'host') {
        midGrid.appendChild(element);
      } else {
        element.classList.add('top-gap');
        standaloneCards.push(element);
      }
    }

    if (topGrid.children.length) container.appendChild(topGrid);
    if (midGrid.children.length) container.appendChild(midGrid);
    for (const element of standaloneCards) container.appendChild(element);

    const logFilter = document.querySelector('[data-system-log-filter]');
    if (logFilter) logFilter.value = previousLogFilter;

    const releaseDetails = document.querySelector('.update-release-details');
    if (releaseDetails) releaseDetails.open = previousReleaseDetailsOpen;

    renderSystemLogs();
  }

  function scheduleSystemOverlayDashboardRedirect() {
    if (systemUpdateOverlayRedirectTimer) return;
    systemUpdateOverlayRedirectTimer = window.setTimeout(() => {
      try { window.sessionStorage.removeItem(systemUpdateOverlaySessionKey); } catch (_) {}
      window.location.href = '/';
    }, 3000);
  }

  function clearSystemOverlayDashboardRedirect() {
    if (!systemUpdateOverlayRedirectTimer) return;
    window.clearTimeout(systemUpdateOverlayRedirectTimer);
    systemUpdateOverlayRedirectTimer = null;
  }

  function systemUpdateOverlayWasStartedHere() {
    try {
      return window.sessionStorage.getItem(systemUpdateOverlaySessionKey) === '1';
    } catch (_) {
      return false;
    }
  }

  function markSystemUpdateOverlayStarted() {
    try {
      window.sessionStorage.setItem(systemUpdateOverlaySessionKey, '1');
    } catch (_) {}
  }

  function clearSystemUpdateOverlayStarted() {
    try {
      window.sessionStorage.removeItem(systemUpdateOverlaySessionKey);
    } catch (_) {}
  }

  function renderSystemUpdateOverlay(runner) {
    const overlay = document.getElementById('updateOverlay');
    if (!overlay) return;

    const badge = document.getElementById('updateOverlayBadge');
    const title = document.getElementById('updateOverlayTitle');
    const subtitle = document.getElementById('updateOverlaySubtitle');
    const percent = document.getElementById('updateOverlayProgressPercent');
    const fill = document.getElementById('updateOverlayProgressFill');
    const message = document.getElementById('updateOverlayMessage');
    const actionLink = document.getElementById('updateOverlayActionLink');

    const running = !!runner?.running;
    if (running) {
      clearSystemOverlayDashboardRedirect();
      overlay.hidden = false;
      if (badge) {
        badge.textContent = runner.status === 'starting' ? 'Startet …' : 'Läuft';
        badge.className = runner.status === 'starting' ? 'status-pill status-info' : 'status-pill status-warn';
      }
      if (title) title.textContent = runner.status === 'starting' ? 'Update startet' : 'Update wird installiert';
      if (subtitle) subtitle.textContent = 'Der Dienst kann dabei kurzzeitig nicht erreichbar sein.';
      if (message) message.textContent = runner.message || 'Update läuft …';
      if (actionLink) {
        actionLink.href = '/system/update-progress';
        actionLink.textContent = 'Fortschrittsseite öffnen';
        actionLink.className = 'btn btn-secondary';
      }
      systemSetProgress(fill, percent, runner.progress_percent);
      return;
    }

    if (runner?.status === 'success') {
      if (!systemUpdateOverlayWasStartedHere() && overlay.hidden) {
        clearSystemOverlayDashboardRedirect();
        return;
      }
      overlay.hidden = false;
      if (badge) {
        badge.textContent = 'Erfolgreich';
        badge.className = 'status-pill status-good';
      }
      if (title) title.textContent = 'Update abgeschlossen';
      if (subtitle) subtitle.textContent = 'PV2Hash wurde erfolgreich aktualisiert. Weiterleitung zum Dashboard …';
      if (message) message.textContent = runner.message || 'Update erfolgreich abgeschlossen.';
      if (actionLink) {
        actionLink.href = '/';
        actionLink.textContent = 'Zum Dashboard';
        actionLink.className = 'btn btn-info';
      }
      systemSetProgress(fill, percent, runner.progress_percent ?? 100);
      scheduleSystemOverlayDashboardRedirect();
      return;
    }

    if (runner?.status === 'error') {
      clearSystemOverlayDashboardRedirect();
      clearSystemUpdateOverlayStarted();
      overlay.hidden = false;
      if (badge) {
        badge.textContent = 'Fehler';
        badge.className = 'status-pill status-bad';
      }
      if (title) title.textContent = 'Update fehlgeschlagen';
      if (subtitle) subtitle.textContent = 'Bitte prüfe die Details auf der Systemseite.';
      if (message) message.textContent = runner.message || runner.last_error || 'Update fehlgeschlagen.';
      if (actionLink) {
        actionLink.href = '/system';
        actionLink.textContent = 'Zur Systemseite';
        actionLink.className = 'btn btn-secondary';
      }
      systemSetProgress(fill, percent, runner.progress_percent ?? 100);
    }
  }

  function systemUserIsEditingImportForm() {
    const form = document.querySelector('[data-system-config-import-form]');
    if (!form) return false;
    const fileInput = form.querySelector('input[type="file"]');
    return Boolean((fileInput && fileInput.files && fileInput.files.length) || document.activeElement?.closest('[data-system-config-import-form]'));
  }

  async function loadSystemModel(options = {}) {
    const root = document.querySelector('[data-system-root]');
    if (!root || systemModelLoading || document.hidden) return;
    if (!options.force && systemUserIsEditingImportForm()) return;
    systemModelLoading = true;
    try {
      const response = await fetch('/api/system/model', { headers: { 'Accept': 'application/json' }, cache: 'no-store' });
      const model = await readJsonResponse(response, 'Systemdaten konnten nicht geladen werden.');
      renderSystemModel(model);
    } catch (error) {
      const container = document.querySelector('[data-system-model-container]');
      if (container) {
        container.innerHTML = '<section class="card"><div class="card-head"><h2>System</h2></div><p class="update-error">Systemdaten konnten nicht geladen werden.</p></section>';
      }
      if (window.showToast) window.showToast('error', error.message || 'Systemdaten konnten nicht geladen werden.');
    } finally {
      systemModelLoading = false;
    }
  }

  function systemRenderLogLine(line) {
    const parts = String(line || '').split(' | ');
    if (parts.length < 4) {
      return `<div class="log-row"><div class="log-message-full">${systemEscapeHtml(line)}</div></div>`;
    }

    const timestamp = parts[0];
    const level = parts[1].trim();
    const logger = parts[2];
    const message = parts.slice(3).join(' | ');
    const levelClass = {
      ERROR: 'log-level-error',
      WARNING: 'log-level-warning',
      INFO: 'log-level-info',
      DEBUG: 'log-level-debug',
    }[level] || '';

    return `
      <div class="log-row">
        <div class="log-col-time">${systemEscapeHtml(timestamp)}</div>
        <div class="log-col-level"><span class="log-level ${levelClass}">${systemEscapeHtml(level)}</span></div>
        <div class="log-col-logger">${systemEscapeHtml(logger)}</div>
        <div class="log-col-message">${systemEscapeHtml(message)}</div>
      </div>
    `;
  }

  function renderSystemLogs() {
    const consoleEl = document.querySelector('[data-system-log-console]');
    if (!consoleEl) return;

    const filterInput = document.querySelector('[data-system-log-filter]');
    const filter = String(filterInput?.value || '').trim().toLowerCase();
    const lines = filter
      ? systemLogLines.filter((line) => String(line).toLowerCase().includes(filter))
      : systemLogLines;

    consoleEl.innerHTML = lines.length
      ? lines.map(systemRenderLogLine).join('')
      : '<div class="log-row"><div class="log-message-full">Keine Logzeilen vorhanden.</div></div>';

    const wrap = document.querySelector('[data-system-console-wrap]');
    if (wrap && systemAutoScroll) {
      wrap.scrollTop = wrap.scrollHeight;
    }
  }

  async function loadSystemLogs() {
    const root = document.querySelector('[data-system-root]');
    if (!root || document.hidden || !systemLogsVisible) return;
    try {
      const response = await fetch('/api/logs', { cache: 'no-store' });
      const data = await readJsonResponse(response, 'Logs konnten nicht geladen werden.');
      systemLogLines = Array.isArray(data.lines) ? data.lines : [];
      renderSystemLogs();
    } catch (error) {
      const consoleEl = document.querySelector('[data-system-log-console]');
      if (consoleEl) {
        consoleEl.innerHTML = '<div class="log-row"><div class="log-message-full">Fehler beim Laden der Logs.</div></div>';
      }
    }
  }

  function startSystemRefresh() {
    const root = document.querySelector('[data-system-root]');
    if (!root || document.hidden) return;
    stopSystemRefresh();
    systemModelTimer = window.setInterval(loadSystemModel, 5000);
    if (systemLogsVisible) {
      systemLogTimer = window.setInterval(loadSystemLogs, 5000);
    }
  }

  function stopSystemRefresh() {
    if (systemModelTimer) {
      window.clearInterval(systemModelTimer);
      systemModelTimer = null;
    }
    if (systemLogTimer) {
      window.clearInterval(systemLogTimer);
      systemLogTimer = null;
    }
  }

  async function systemRunUpdateCheck(button) {
    if (button?.dataset.busy === '1') return;
    const restore = setButtonBusy(button, 'Prüft …');
    try {
      const response = await fetch('/api/system/update-check', {
        method: 'POST',
        headers: { 'Accept': 'application/json' },
      });
      await readJsonResponse(response, 'Update-Prüfung fehlgeschlagen.');
      await loadSystemModel({ force: true });
      if (window.showToast) window.showToast('success', 'Update-Prüfung abgeschlossen.');
    } catch (error) {
      if (window.showToast) window.showToast('error', error.message || 'Update-Prüfung fehlgeschlagen.');
    } finally {
      restore();
    }
  }

  async function systemStartUpdate(button) {
    if (button?.dataset.busy === '1') return;
    const release = systemLastUpdateStatus && (systemLastUpdateStatus.release_version_full || systemLastUpdateStatus.release_tag)
      ? (systemLastUpdateStatus.release_version_full || systemLastUpdateStatus.release_tag)
      : 'das aktuelle Release';

    if (!window.confirm(`PV2Hash wirklich auf ${release} aktualisieren? Der Dienst wird dabei neu gestartet.`)) {
      return;
    }

    const restore = setButtonBusy(button, 'Startet …');
    try {
      const response = await fetch('/api/system/self-update', {
        method: 'POST',
        headers: { 'Accept': 'application/json' },
      });
      await readJsonResponse(response, 'Update konnte nicht gestartet werden.');
      markSystemUpdateOverlayStarted();
      const overlay = document.getElementById('updateOverlay');
      if (overlay) overlay.hidden = false;
      await loadSystemModel({ force: true });
      if (window.showToast) window.showToast('success', 'Update wurde gestartet.');
    } catch (error) {
      if (window.showToast) window.showToast('error', error.message || 'Update konnte nicht gestartet werden.');
    } finally {
      restore();
    }
  }

  async function systemReloadRuntime(button) {
    if (button?.dataset.busy === '1') return;
    const restore = setButtonBusy(button, 'Lädt …');
    try {
      const data = await postJson('/api/system/reload', {});
      if (data.model) renderSystemModel(data.model);
      if (window.showToast) window.showToast('success', data.message || 'Runtime wurde neu geladen.');
    } catch (error) {
      if (window.showToast) window.showToast('error', error.message || 'Runtime konnte nicht neu geladen werden.');
    } finally {
      restore();
    }
  }

  async function systemSaveLogLevel(button) {
    if (button?.dataset.busy === '1') return;
    const select = document.querySelector('[data-system-log-level]');
    const restore = setButtonBusy(button, 'Speichert …');
    try {
      const data = await postJson('/api/system/logging', { log_level: select?.value || 'INFO' });
      if (data.model) renderSystemModel(data.model);
      if (window.showToast) window.showToast('success', data.message || 'Log-Level gespeichert.');
    } catch (error) {
      if (window.showToast) window.showToast('error', error.message || 'Log-Level konnte nicht gespeichert werden.');
    } finally {
      restore();
    }
  }

  async function systemImportConfig(form) {
    if (!form || form.dataset.busy === '1') return;
    const button = form.querySelector('[data-submit-button]');
    const restore = setButtonBusy(button, 'Importiert …');
    setFormBusy(form, true);
    try {
      const data = await postForm('/api/system/config/import', form);
      if (data.model) renderSystemModel(data.model);
      if (window.showToast) window.showToast('success', data.message || 'Konfiguration importiert.');
    } catch (error) {
      if (window.showToast) window.showToast('error', error.message || 'Konfiguration konnte nicht importiert werden.');
    } finally {
      setFormBusy(form, false);
      restore();
    }
  }

  function setupSystemPage() {
    const root = document.querySelector('[data-system-root]');
    if (!root || root.dataset.systemPageReady === '1') return;
    root.dataset.systemPageReady = '1';

    root.addEventListener('click', (event) => {
      const updateCheck = event.target.closest('[data-system-update-check]');
      if (updateCheck) {
        event.preventDefault();
        systemRunUpdateCheck(updateCheck);
        return;
      }

      const updateStart = event.target.closest('[data-system-update-start]');
      if (updateStart) {
        event.preventDefault();
        systemStartUpdate(updateStart);
        return;
      }

      const action = event.target.closest('[data-system-action]');
      if (action) {
        event.preventDefault();
        if (action.dataset.systemAction === 'reload_runtime') systemReloadRuntime(action);
        return;
      }

      const logLevelSave = event.target.closest('[data-system-log-level-save]');
      if (logLevelSave) {
        event.preventDefault();
        systemSaveLogLevel(logLevelSave);
        return;
      }

      const clearFilter = event.target.closest('[data-system-log-filter-clear]');
      if (clearFilter) {
        event.preventDefault();
        const input = document.querySelector('[data-system-log-filter]');
        if (input) input.value = '';
        renderSystemLogs();
        return;
      }

      const scrollToggle = event.target.closest('[data-system-log-scroll-toggle]');
      if (scrollToggle) {
        event.preventDefault();
        systemAutoScroll = !systemAutoScroll;
        scrollToggle.textContent = systemAutoScroll ? 'STOPPEN SCROLLING' : 'STARTEN SCROLLING';
        if (systemAutoScroll) {
          const wrap = document.querySelector('[data-system-console-wrap]');
          if (wrap) wrap.scrollTop = wrap.scrollHeight;
        }
        return;
      }

      const visibilityToggle = event.target.closest('[data-system-log-visibility-toggle]');
      if (visibilityToggle) {
        event.preventDefault();
        systemLogsVisible = !systemLogsVisible;
        const wrap = document.querySelector('[data-system-console-wrap]');
        if (wrap) wrap.style.display = systemLogsVisible ? 'block' : 'none';
        visibilityToggle.textContent = systemLogsVisible ? 'AUSBLENDEN LOGS' : 'EINBLENDEN LOGS';
        if (systemLogsVisible) {
          loadSystemLogs();
          startSystemRefresh();
        } else if (systemLogTimer) {
          window.clearInterval(systemLogTimer);
          systemLogTimer = null;
        }
      }
    });

    root.addEventListener('input', (event) => {
      if (event.target.closest('[data-system-log-filter]')) {
        renderSystemLogs();
      }
    });

    root.addEventListener('submit', (event) => {
      const form = event.target.closest('[data-system-config-import-form]');
      if (!form) return;
      event.preventDefault();
      systemImportConfig(form);
    });

    document.addEventListener('visibilitychange', () => {
      if (document.hidden) {
        stopSystemRefresh();
      } else {
        loadSystemModel({ force: true });
        loadSystemLogs();
        startSystemRefresh();
      }
    });

    loadSystemModel({ force: true });
    loadSystemLogs();
    startSystemRefresh();
  }


  function setupVersionStatusRefresh() {
    const link = document.querySelector('[data-versionstatus]');
    if (!link) return;

    document.addEventListener('visibilitychange', () => {
      if (document.hidden) {
        stopVersionStatusRefresh();
      } else {
        refreshVersionStatus();
        startVersionStatusRefresh();
      }
    });

    refreshVersionStatus();
    startVersionStatusRefresh();
  }

  document.addEventListener('DOMContentLoaded', () => {
    for (const button of document.querySelectorAll('[data-miner-action][data-disable-when-control="1"]')) {
      button.dataset.originalTitle = button.getAttribute('title') || '';
    }
    for (const form of document.querySelectorAll('[data-miner-config-form]')) {
      // Action availability is based on the last saved server state, not on unsaved form edits.
      syncMinerActionGuards(form);
    }

    const params = new URLSearchParams(window.location.search);
    openAndScrollToMiner(params.get('miner_id'));
    setupBackendConnectivityMonitor();
    setupMinerLiveRefresh();
    setupDashboardLiveRefresh();
    setupSourcesPage();
    setupSettingsPage();
    setupSystemPage();
    setupVersionStatusRefresh();
  });
})();

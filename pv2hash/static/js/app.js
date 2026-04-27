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

  async function runSourceAction(form, button) {
    if (!form || !button || form.dataset.busy === '1') return;

    const payload = new FormData(form);
    payload.set('source_id', button.dataset.sourceId || '');
    payload.set('action_id', button.dataset.sourceAction || '');

    const restore = setButtonBusy(button, 'Suche …');
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
    setupMinerLiveRefresh();
    setupDashboardLiveRefresh();
    setupSourcesPage();
    setupVersionStatusRefresh();
  });
})();

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
    const safeMinerId = window.CSS && window.CSS.escape ? window.CSS.escape(String(minerId)) : String(minerId).replace(/"/g, '\"');
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
    return String(value).replace(/"/g, '\\"');
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



  function syncTypeSections(selectId, attributeName) {
    const select = document.getElementById(selectId);
    if (!select) return;

    const update = () => {
      const current = select.value;
      document.querySelectorAll(`[${attributeName}]`).forEach((element) => {
        const visibleFor = (element.getAttribute(attributeName) || '')
          .split(',')
          .map((item) => item.trim())
          .filter(Boolean);
        element.hidden = !visibleFor.includes(current);
      });
    };

    select.addEventListener('change', update);
    update();
  }

  function bindPanelToggles() {
    document.querySelectorAll('[data-toggle-target]').forEach((button) => {
      button.addEventListener('click', () => {
        const target = document.getElementById(button.getAttribute('data-toggle-target'));
        if (!target) return;

        const willShow = target.hidden;
        target.hidden = !willShow;
        button.textContent = willShow ? 'Konfiguration verbergen' : 'Konfigurieren';
      });
    });
  }

  function syncBatteryEnabled() {
    const typeSelect = document.getElementById('batteryTypeSelect');
    const enabledCheckbox = document.getElementById('batteryEnabledCheckbox');
    if (!typeSelect || !enabledCheckbox) return;

    const update = () => {
      const noBattery = typeSelect.value === 'none';
      if (noBattery) enabledCheckbox.checked = false;
      enabledCheckbox.disabled = noBattery;
    };

    typeSelect.addEventListener('change', update);
    update();
  }

  function serializeDirtyFields(container) {
    const fields = Array.from(container.querySelectorAll('input, select, textarea'))
      .filter((field) => field.name && !field.disabled && !['submit', 'button', 'reset', 'file'].includes(field.type));

    return JSON.stringify(fields.map((field) => {
      if (field.type === 'checkbox' || field.type === 'radio') return [field.name, field.checked];
      return [field.name, field.value];
    }));
  }

  function resetDirtyScope(scope) {
    if (!scope) return;
    scope.dataset.dirtyBaseline = serializeDirtyFields(scope);
    scope.dataset.dirtyTouched = '0';
    const hint = scope.querySelector('[data-dirty-indicator]');
    if (hint) hint.hidden = true;
    for (const button of scope.querySelectorAll('[data-dirty-submit]')) {
      button.classList.remove('is-dirty');
    }
    scope.dataset.dirty = 'false';
  }

  function refreshDirtyScope(scope) {
    if (!scope) return;
    const touched = scope.dataset.dirtyTouched === '1';
    const baseline = scope.dataset.dirtyBaseline || serializeDirtyFields(scope);
    const dirty = serializeDirtyFields(scope) !== baseline;
    const visibleDirty = touched && dirty;
    const hint = scope.querySelector('[data-dirty-indicator]');
    if (hint) hint.hidden = !visibleDirty;
    for (const button of scope.querySelectorAll('[data-dirty-submit]')) {
      button.classList.toggle('is-dirty', visibleDirty);
    }
    scope.dataset.dirty = visibleDirty ? 'true' : 'false';
  }

  function bindDirtyScopes() {
    document.querySelectorAll('[data-dirty-scope]').forEach((scope) => {
      resetDirtyScope(scope);

      scope.querySelectorAll('input, select, textarea').forEach((field) => {
        if (!field.name || ['submit', 'button', 'reset', 'file'].includes(field.type)) return;
        const onUserChange = () => {
          scope.dataset.dirtyTouched = '1';
          refreshDirtyScope(scope);
        };
        field.addEventListener('input', onUserChange);
        field.addEventListener('change', onUserChange);
      });

      window.requestAnimationFrame(() => resetDirtyScope(scope));
      window.setTimeout(() => resetDirtyScope(scope), 120);
    });
  }

  function updateSmaDeviceSelect(devices, selectedSerial) {
    const select = document.querySelector('[data-sma-device-select]');
    if (!select || select.dataset.userTouched === '1') return;

    const normalizedSelected = String(selectedSerial || select.value || '').trim();
    const currentOptions = JSON.stringify(Array.from(select.options).map((option) => [option.value, option.textContent]));
    const nextDevices = Array.isArray(devices) ? devices : [];
    const nextOptions = nextDevices.map((item) => [String(item.serial_number || ''), String(item.label || item.serial_number || '')]);
    const nextSignature = JSON.stringify(nextOptions);
    if (currentOptions === nextSignature && select.value === normalizedSelected) return;

    select.innerHTML = '';
    if (!normalizedSelected) {
      const placeholder = document.createElement('option');
      placeholder.value = '';
      placeholder.textContent = 'SMA-Gerät auswählen …';
      placeholder.disabled = true;
      placeholder.selected = true;
      select.appendChild(placeholder);
    }

    for (const [value, label] of nextOptions) {
      if (!value) continue;
      const option = document.createElement('option');
      option.value = value;
      option.textContent = label;
      option.selected = value === normalizedSelected;
      select.appendChild(option);
    }
  }

  function updateSourcesGuiModels(data) {
    window.pv2hashSourcesGuiModels = Array.isArray(data?.gui_models)
      ? data.gui_models
      : Array.isArray(data?.sources)
        ? data.sources
        : [];
  }

  function updateSourcesSummary(data) {
    if (!data) return;
    updateSourcesGuiModels(data);

    const source = data.source || {};
    setText('[data-source-summary-field="profile"]', source.profile_label || '—');
    setText('[data-source-summary-field="device"]', source.device_label || '—');
    setText('[data-source-summary-field="serial"]', source.serial_number || '—');
    setText('[data-source-summary-field="susy"]', source.susy_id || '—');

    const sourceCard = document.getElementById('napMeasurementCard');
    if (sourceCard) {
      sourceCard.dataset.sourceType = source.type || '';
      sourceCard.dataset.sourceDeviceLabel = source.device_label || '';
      sourceCard.dataset.sourceSerial = source.serial_number || '';
      sourceCard.dataset.sourceSusy = source.susy_id || '';
    }

    const battery = data.battery || {};
    setText('[data-battery-summary-field="profile"]', battery.profile_label || '—');
    setText('[data-battery-summary-field="status"]', battery.status_label || 'deaktiviert');

    updateSmaDeviceSelect(source.discovered_devices, source.selected_device_serial);
  }

  async function submitSourcesConfig(form, submitter) {
    if (!form || form.dataset.busy === '1') return;

    const restore = setButtonBusy(submitter || form.querySelector('[type="submit"]'), 'Speichert …');
    setFormBusy(form, true);

    try {
      const data = await postForm('/api/sources/config', form);
      updateSourcesSummary(data);
      for (const scope of form.querySelectorAll('[data-dirty-scope]')) resetDirtyScope(scope);
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

  async function refreshSourcesLiveData() {
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
      updateSourcesSummary(data);
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

    syncTypeSections('sourceTypeSelect', 'data-source-types');
    syncTypeSections('batteryTypeSelect', 'data-battery-types');
    bindPanelToggles();
    syncBatteryEnabled();
    bindDirtyScopes();

    const form = document.querySelector('[data-sources-config-form]');
    if (form) {
      const select = form.querySelector('[data-sma-device-select]');
      if (select) {
        select.addEventListener('change', () => {
          select.dataset.userTouched = '1';
        });
      }

      form.addEventListener('submit', (event) => {
        event.preventDefault();
        submitSourcesConfig(form, event.submitter);
      });
    }

    const params = new URLSearchParams(window.location.search);
    if (params.get('saved') === '1') {
      window.showToast('success', 'Messungen gespeichert.');
      window.history.replaceState({}, document.title, window.location.pathname);
    } else if (params.get('serial_required') === '1') {
      window.showToast('warning', 'Bitte ein SMA-Gerät bzw. eine Seriennummer auswählen.');
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

    refreshSourcesLiveData();
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

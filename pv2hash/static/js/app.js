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
  });
})();

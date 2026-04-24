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

  async function postJson(url, payload) {
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
      body: JSON.stringify(payload || {}),
    });

    let data = {};
    try {
      data = await response.json();
    } catch (_) {
      data = { status: response.ok ? 'ok' : 'error', message: response.statusText || 'Unbekannte Antwort.' };
    }

    if (!response.ok || data.status === 'error') {
      throw new Error(data.message || 'Aktion fehlgeschlagen.');
    }
    return data;
  }

  window.runMinerAction = async function runMinerAction(button) {
    const minerId = button.dataset.minerId;
    const actionName = button.dataset.actionName;
    const confirmText = button.dataset.confirmText;
    if (!minerId || !actionName) return;
    if (confirmText && !window.confirm(confirmText)) return;

    const oldText = button.textContent;
    button.disabled = true;
    button.textContent = 'Läuft …';
    try {
      const data = await postJson(`/api/miner/${encodeURIComponent(minerId)}/action`, { action_name: actionName });
      window.showToast('success', data.message || 'Aktion erfolgreich ausgeführt.');
    } catch (error) {
      window.showToast('error', error.message || 'Aktion fehlgeschlagen.');
    } finally {
      button.disabled = false;
      button.textContent = oldText;
    }
  };

  document.addEventListener('click', (event) => {
    const button = event.target.closest('[data-miner-action]');
    if (!button || button.disabled) return;
    event.preventDefault();
    window.runMinerAction(button);
  });
})();

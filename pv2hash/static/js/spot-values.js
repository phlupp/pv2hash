(() => {
  const previousValues = new Map();
  let refreshTimer = null;
  let refreshRunning = false;

  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function valueSignature(row) {
    try {
      return JSON.stringify(row && Object.prototype.hasOwnProperty.call(row, 'value') ? row.value : null);
    } catch (_) {
      return String(row?.value_text || '');
    }
  }

  function renderSection(section) {
    const rows = Array.isArray(section?.rows) ? section.rows : [];
    const body = rows.map((row) => {
      const key = String(row?.key || '');
      const signature = valueSignature(row);
      const previous = previousValues.get(key);
      const changed = previous !== undefined && previous !== signature;
      previousValues.set(key, signature);
      const unit = row?.unit ? ` ${escapeHtml(row.unit)}` : '';
      return `
        <tr class="${changed ? 'spot-value-changed' : ''}" data-spot-value-key="${escapeHtml(key)}">
          <td><code>${escapeHtml(key)}</code></td>
          <td class="spot-value-cell"><span>${escapeHtml(row?.value_text ?? row?.value ?? '—')}${unit}</span></td>
          <td>${escapeHtml(row?.type || '')}</td>
          <td>${escapeHtml(row?.source || '')}</td>
        </tr>
      `;
    }).join('');

    return `
      <section class="spot-values-section top-gap" data-spot-values-section="${escapeHtml(section?.id || '')}">
        <div class="card-head compact">
          <div>
            <h2>${escapeHtml(section?.title || section?.id || 'Section')}</h2>
            ${section?.subtitle ? `<p class="card-subtitle">${escapeHtml(section.subtitle)}</p>` : ''}
          </div>
          <span class="badge">${rows.length} Werte</span>
        </div>
        <div class="table-wrap">
          <table class="spot-values-table">
            <thead>
              <tr>
                <th>Kanal</th>
                <th>Wert</th>
                <th>Typ</th>
                <th>Quelle</th>
              </tr>
            </thead>
            <tbody>${body || '<tr><td colspan="4">Keine Werte vorhanden.</td></tr>'}</tbody>
          </table>
        </div>
      </section>
    `;
  }

  function setUpdatedText(payload) {
    const el = document.querySelector('[data-spot-values-updated]');
    if (!el) return;
    const raw = payload?.updated_at;
    if (!raw) {
      el.textContent = '—';
      return;
    }
    const date = new Date(raw);
    el.textContent = Number.isNaN(date.getTime()) ? String(raw) : `Update ${date.toLocaleTimeString('de-DE')}`;
  }

  async function refreshSpotValues() {
    const root = document.querySelector('[data-spot-values-root]');
    if (!root || document.hidden || refreshRunning) return;
    refreshRunning = true;
    try {
      const response = await fetch('/api/spot-values', { headers: { 'Accept': 'application/json' }, cache: 'no-store' });
      const payload = await response.json();
      if (!response.ok || payload.status === 'error') throw new Error(payload.message || response.statusText || 'Spot-Values konnten nicht geladen werden.');
      const container = document.querySelector('[data-spot-values-sections]');
      if (!container || document.hidden) return;
      const sections = Array.isArray(payload.sections) ? payload.sections : [];
      container.innerHTML = sections.map(renderSection).join('') || '<p class="muted">Keine Spot-Values vorhanden.</p>';
      setUpdatedText(payload);
      window.setTimeout(() => {
        for (const row of document.querySelectorAll('.spot-value-changed')) row.classList.remove('spot-value-changed');
      }, 1500);
    } catch (error) {
      if (window.showToast) window.showToast('warning', error.message || 'Spot-Values konnten nicht aktualisiert werden.', { timeout: 5000 });
      console.debug('PV2Hash spot-values refresh failed:', error);
    } finally {
      refreshRunning = false;
    }
  }

  function startRefresh() {
    const root = document.querySelector('[data-spot-values-root]');
    if (!root || document.hidden) return;
    stopRefresh();
    const seconds = Math.max(2, Number(root.dataset.refreshSeconds || 5));
    refreshTimer = window.setInterval(refreshSpotValues, seconds * 1000);
  }

  function stopRefresh() {
    if (refreshTimer) {
      window.clearInterval(refreshTimer);
      refreshTimer = null;
    }
  }

  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      stopRefresh();
    } else {
      refreshSpotValues();
      startRefresh();
    }
  });

  document.addEventListener('DOMContentLoaded', () => {
    if (!document.querySelector('[data-spot-values-root]')) return;
    refreshSpotValues();
    startRefresh();
  });
})();

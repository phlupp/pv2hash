(() => {
  const modelUrl = '/api/miners/socket-assignment/model';

  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#039;');
  }

  function field(name, value) {
    return escapeHtml(value == null ? '' : value);
  }

  function socketStateText(runtime) {
    if (!runtime || !runtime.key && !runtime.id) return 'Keine Live-Daten';
    const parts = [];
    if (runtime.reachable) {
      parts.push(runtime.is_on === true ? 'Ein' : runtime.is_on === false ? 'Aus' : 'Zustand unbekannt');
    } else {
      parts.push('Nicht erreichbar');
    }
    if (runtime.power_w != null) {
      const power = Number(runtime.power_w);
      if (Number.isFinite(power)) parts.push(`${power.toFixed(0)} W`);
    }
    return parts.join(' · ');
  }

  function renderMinerSection(miner) {
    const socket = miner.socket || {};
    const options = miner.options || [];
    const runtimeText = socketStateText(miner.runtime || {});
    const modeOptions = miner.mode_options || [];
    const selectedMode = socket.mode || 'measure_only';
    const selectedSocketId = socket.socket_id || '';
    const missing = miner.socket_missing ? '<small class="field-help">Die gespeicherte Steckdose wurde nicht gefunden. Bitte Zuordnung entfernen oder eine freie Steckdose wählen.</small>' : '';
    const optionHtml = ['<option value="">Keine Steckdose</option>'].concat(options.map((item) => {
      const selected = item.id === selectedSocketId ? ' selected' : '';
      return `<option value="${field('id', item.id)}"${selected}>${escapeHtml(item.label)}</option>`;
    })).join('');
    const modeHtml = modeOptions.map((item) => {
      const selected = item.value === selectedMode ? ' selected' : '';
      return `<option value="${field('value', item.value)}"${selected}>${escapeHtml(item.label)}</option>`;
    }).join('');
    return `
      <h3 class="section-title">Stromversorgung</h3>
      <div class="details-section" data-socket-assignment-section>
        <div class="miner-field-grid">
          <div class="field gui-field gui-field-half">
            <label>Steckdose</label>
            <select name="socket.socket_id" data-miner-socket-select>
              ${optionHtml}
            </select>
            <small class="field-help">Nur freie reale Sockets werden angezeigt. Simulator-Sockets und Automatik-Sockets sind ausgeschlossen.</small>
            ${missing}
          </div>
          <div class="field gui-field gui-field-half">
            <label>Betriebsart</label>
            <select name="socket.mode" data-miner-socket-mode>
              ${modeHtml}
            </select>
            <small class="field-help">„Nur messen“ schaltet nicht. „Messen und schalten“ bereitet die spätere automatische Stromsteuerung vor.</small>
          </div>
          <div class="field gui-field gui-field-half" data-switching-field>
            <label>Einschaltverzögerung</label>
            <input type="number" name="socket.startup_delay_seconds" min="0" step="1" value="${field('startup', socket.startup_delay_seconds ?? 60)}">
            <small class="field-help">Sekunden nach Socket-Einschalten, bevor der Miner starten darf. Standard: 60 s.</small>
          </div>
          <div class="field gui-field gui-field-half" data-switching-field>
            <label>Ausschalten nach Off</label>
            <input type="number" name="socket.power_off_after_off_seconds" min="${field('min', miner.min_power_off_after_off_seconds || 600)}" step="60" value="${field('poweroff', socket.power_off_after_off_seconds ?? 3600)}">
            <small class="field-help">Sekunden im Profil off bis Socket-Off. Minimum: 600 s, Standard: 3600 s.</small>
          </div>
        </div>
        <div class="miner-meta" style="margin-top:.75rem;">
          <span class="pill neutral">Socket: ${escapeHtml(miner.socket_name || 'keine')}</span>
          <span class="pill neutral">${escapeHtml(runtimeText)}</span>
        </div>
      </div>
    `;
  }

  function syncModeVisibility(section) {
    const mode = section.querySelector('[data-miner-socket-mode]');
    if (!mode) return;
    const switching = mode.value === 'switching';
    for (const item of section.querySelectorAll('[data-switching-field]')) {
      item.hidden = !switching;
    }
  }

  function insertMinerSections(model) {
    for (const form of document.querySelectorAll('form[data-miner-config-form]')) {
      const minerId = form.dataset.minerId;
      const miner = model.miners && model.miners[minerId];
      if (!miner || !miner.show_section || form.querySelector('[data-socket-assignment-section]')) continue;
      const driverTitle = Array.from(form.querySelectorAll('h3.section-title')).find((item) => item.textContent.includes('Treiber-Konfiguration'));
      if (!driverTitle) continue;
      driverTitle.insertAdjacentHTML('beforebegin', renderMinerSection(miner));
      const section = form.querySelector('[data-socket-assignment-section]');
      if (section) {
        const mode = section.querySelector('[data-miner-socket-mode]');
        if (mode) mode.addEventListener('change', () => syncModeVisibility(section));
        syncModeVisibility(section);
      }
    }
  }

  function renderSocketAssignments(model) {
    for (const card of document.querySelectorAll('[data-socket-card]')) {
      const socketId = card.dataset.socketId;
      const item = model.sockets && model.sockets[socketId];
      if (!item || card.querySelector('[data-socket-assignment-pill]')) continue;
      const meta = card.querySelector('summary .miner-meta');
      if (!meta) continue;
      const assignment = item.assignment || {};
      const pill = document.createElement('span');
      pill.className = `pill ${assignment.class || 'neutral'}`;
      pill.dataset.socketAssignmentPill = '1';
      pill.textContent = assignment.role === 'miner'
        ? `Zugeordnet: ${assignment.target_name || assignment.target_id}`
        : 'Frei';
      meta.appendChild(pill);
      if (assignment.role === 'miner') {
        const automatik = card.querySelector('input[name="control_enabled"]');
        if (automatik) {
          automatik.checked = false;
          automatik.disabled = true;
          const help = document.createElement('small');
          help.className = 'field-help';
          help.textContent = 'Für Miner reservierte Sockets können nicht für Verbraucher-Automatik verwendet werden.';
          automatik.closest('.field')?.appendChild(help);
        }
      }
    }
  }

  async function loadModel() {
    const hasMinerPage = document.querySelector('form[data-miner-config-form]');
    const hasSocketPage = document.querySelector('[data-socket-card]');
    if (!hasMinerPage && !hasSocketPage) return;
    try {
      const response = await fetch(modelUrl, {headers: {'accept': 'application/json'}});
      if (!response.ok) return;
      const data = await response.json();
      if (!data || data.status !== 'ok') return;
      insertMinerSections(data);
      renderSocketAssignments(data);
    } catch (error) {
      console.debug('Socket assignment model unavailable', error);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', loadModel);
  } else {
    loadModel();
  }
})();

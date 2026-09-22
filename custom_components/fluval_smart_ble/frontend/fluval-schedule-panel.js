// Sidebar panel for editing Fluval Smart lights' on-device Auto and Pro
// schedules. A dependency-free web component: Home Assistant hands it the
// `hass`, `narrow` and `panel` properties, and it talks to the integration
// through the fluval_smart_ble/* websocket commands (see api.py).

const DOMAIN = "fluval_smart_ble";
const PRO_MIN_POINTS = 4;
const PRO_MAX_POINTS = 10;
const DAY_MINUTES = 24 * 60;

const CHANNEL_COLORS = {
  Red: "#e53935",
  Green: "#43a047",
  Blue: "#1e88e5",
  White: "#9e9e9e",
  Pink: "#ec407a",
  Cyan: "#00acc1",
  Purple: "#8e24aa",
  "Cold White": "#64b5f6",
  "Pure White": "#bdbdbd",
  "Warm White": "#ffa726",
};
const FALLBACK_COLORS = ["#5c6bc0", "#26a69a", "#d4e157", "#8d6e63", "#78909c"];

const MODE_LABELS = { manual: "Manual", auto: "Auto", pro: "Pro", sun_sync: "Sun sync" };
const SOURCE_LABELS = {
  light: "Loaded from the light.",
  saved: "The light isn't in this mode, so this is the schedule last saved from Home Assistant.",
  default: "No schedule has been read from the light or saved yet, so these are defaults.",
};

const escapeHtml = (value) =>
  String(value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const toMinutes = (time) => {
  const [h, m] = String(time).split(":").map(Number);
  return h * 60 + m;
};
const fromMinutes = (minutes) =>
  `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;

const channelColor = (name, index) => CHANNEL_COLORS[name] || FALLBACK_COLORS[index % FALLBACK_COLORS.length];

const clone = (value) => JSON.parse(JSON.stringify(value));

// The Auto schedule sun sync would push, mirroring build_sun_schedule().
function sunSchedule(cfg, sunrise, sunset) {
  const clamp = (m) => Math.max(0, Math.min(DAY_MINUTES - 1, m));
  const sr0 = clamp(toMinutes(sunrise) + Number(cfg.sunrise_offset));
  const sr1 = clamp(sr0 + Number(cfg.sunrise_duration));
  const ss0 = clamp(toMinutes(sunset) + Number(cfg.sunset_offset));
  const ss1 = clamp(ss0 + Number(cfg.sunset_duration));
  return {
    sunrise_start: fromMinutes(sr0),
    sunrise_end: fromMinutes(sr1),
    sunset_start: fromMinutes(ss0),
    sunset_end: fromMinutes(ss1),
    day: cfg.day,
    night: cfg.night,
    turnoff_enabled: cfg.turnoff_enabled,
    turnoff: cfg.turnoff,
  };
}

const formatDateTime = (iso) =>
  iso ? new Date(iso).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" }) : "never";

// Brightness curves, as [minute, value] vertices per channel.
function autoCurves(auto, channelCount) {
  const sr0 = toMinutes(auto.sunrise_start);
  const sr1 = toMinutes(auto.sunrise_end);
  const ss0 = toMinutes(auto.sunset_start);
  const ss1 = toMinutes(auto.sunset_end);
  const curves = [];
  for (let c = 0; c < channelCount; c++) {
    const day = Number(auto.day[c]);
    const night = Number(auto.night[c]);
    curves.push([[0, night], [sr0, night], [sr1, day], [ss0, day], [ss1, night], [DAY_MINUTES, night]]);
  }
  return curves;
}

function proCurves(points, channelCount) {
  const sorted = points
    .map((p) => ({ at: toMinutes(p.time), values: p.values.map(Number) }))
    .sort((a, b) => a.at - b.at);
  const first = sorted[0];
  const last = sorted[sorted.length - 1];
  // The day wraps: between the last point and the first, brightness keeps
  // interpolating across midnight.
  const span = first.at + DAY_MINUTES - last.at;
  const t = span > 0 ? (DAY_MINUTES - last.at) / span : 0;
  const curves = [];
  for (let c = 0; c < channelCount; c++) {
    const atMidnight = last.values[c] + (first.values[c] - last.values[c]) * t;
    curves.push([[0, atMidnight], ...sorted.map((p) => [p.at, p.values[c]]), [DAY_MINUTES, atMidnight]]);
  }
  return curves;
}

function chartSvg(curves, channels, markers = []) {
  const W = 720;
  const H = 180;
  const PAD_L = 34;
  const PAD_R = 20;
  const PAD_B = 22;
  const PAD_T = 8;
  const x = (m) => PAD_L + (m / DAY_MINUTES) * (W - PAD_L - PAD_R);
  const y = (v) => PAD_T + (1 - v / 100) * (H - PAD_T - PAD_B);
  let grid = "";
  for (let h = 0; h <= 24; h += 3) {
    grid += `<line class="grid" x1="${x(h * 60)}" x2="${x(h * 60)}" y1="${y(100)}" y2="${y(0)}"/>`;
    grid += `<text class="axis" x="${x(h * 60)}" y="${H - 6}" text-anchor="middle">${String(h).padStart(2, "0")}:00</text>`;
  }
  for (const v of [0, 50, 100]) {
    grid += `<line class="grid" x1="${x(0)}" x2="${x(DAY_MINUTES)}" y1="${y(v)}" y2="${y(v)}"/>`;
    grid += `<text class="axis" x="${PAD_L - 6}" y="${y(v) + 4}" text-anchor="end">${v}%</text>`;
  }
  const marks = markers
    .map(
      (m) =>
        `<line class="marker ${m.cls || ""}" x1="${x(m.at)}" x2="${x(m.at)}" y1="${y(100)}" y2="${y(0)}"/>` +
        // Sun markers are labelled at the bottom, clear of the daytime curves.
        `<text class="axis" x="${x(m.at) + 4}" y="${m.cls === "sun" ? y(0) - 6 : y(100) + 12}">${escapeHtml(m.label)}</text>`
    )
    .join("");
  const lines = curves
    .map(
      (curve, c) =>
        `<polyline fill="none" stroke="${channelColor(channels[c], c)}" stroke-width="2.5" stroke-linejoin="round" points="${curve
          .map(([m, v]) => `${x(m).toFixed(1)},${y(v).toFixed(1)}`)
          .join(" ")}"/>`
    )
    .join("");
  const legend = channels
    .map(
      (name, c) =>
        `<span class="legend-item"><span class="swatch" style="background:${channelColor(name, c)}"></span>${escapeHtml(name)}</span>`
    )
    .join("");
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Brightness over the day">${grid}${marks}${lines}</svg><div class="legend">${legend}</div>`;
}

const STYLE = `
  :host {
    display: block;
    min-height: 100vh;
    background: var(--primary-background-color);
    color: var(--primary-text-color);
    font-family: var(--paper-font-body1_-_font-family, Roboto, sans-serif);
  }
  .toolbar {
    display: flex;
    align-items: center;
    height: var(--header-height, 56px);
    padding: 0 12px;
    box-sizing: border-box;
    background: var(--app-header-background-color, var(--primary-color));
    color: var(--app-header-text-color, var(--text-primary-color, #fff));
    border-bottom: var(--app-header-border-bottom, none);
    font-size: 20px;
  }
  .toolbar .title { margin-left: 12px; flex: 1; }
  .toolbar button.icon {
    background: none; border: none; color: inherit; cursor: pointer;
    font-size: 14px; padding: 8px 12px; border-radius: 4px;
  }
  .toolbar button.icon:hover { background: rgba(255,255,255,0.12); }
  .content { max-width: 960px; margin: 0 auto; padding: 16px; box-sizing: border-box; }
  .card {
    background: var(--ha-card-background, var(--card-background-color, #fff));
    border-radius: var(--ha-card-border-radius, 12px);
    border: 1px solid var(--divider-color);
    box-shadow: var(--ha-card-box-shadow, none);
    padding: 16px;
    margin-bottom: 16px;
  }
  h2 { font-size: 18px; font-weight: 500; margin: 0 0 4px; }
  h3 { font-size: 15px; font-weight: 500; margin: 20px 0 8px; }
  .muted { color: var(--secondary-text-color); font-size: 14px; margin: 0; }
  .row { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 16px; }
  .spread { justify-content: space-between; }
  .segmented { display: inline-flex; border: 1px solid var(--divider-color); border-radius: 8px; overflow: hidden; }
  .segmented button {
    background: none; border: none; padding: 8px 16px; cursor: pointer;
    color: var(--primary-text-color); font: inherit; font-size: 14px;
  }
  .segmented button + button { border-left: 1px solid var(--divider-color); }
  .segmented button.active { background: var(--primary-color); color: var(--text-primary-color, #fff); }
  .segmented button:disabled { cursor: default; opacity: 0.6; }
  .tabs { display: flex; gap: 4px; border-bottom: 1px solid var(--divider-color); margin: 0 0 16px; overflow-x: auto; }
  .tabs button {
    background: none; border: none; border-bottom: 2px solid transparent;
    padding: 10px 16px; cursor: pointer; font: inherit; font-size: 15px;
    color: var(--secondary-text-color); white-space: nowrap;
  }
  .tabs button.active { color: var(--primary-color); border-bottom-color: var(--primary-color); }
  @media (max-width: 480px) { .tabs button { padding: 10px 10px; font-size: 14px; } }
  select, input[type="time"], input[type="number"] {
    font: inherit; font-size: 14px; padding: 6px 8px;
    border: 1px solid var(--divider-color); border-radius: 6px;
    background: var(--card-background-color, #fff); color: var(--primary-text-color);
    color-scheme: light dark;
  }
  input[type="number"] { width: 64px; }
  label.field { display: flex; flex-direction: column; gap: 4px; font-size: 13px; color: var(--secondary-text-color); }
  .grid2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; }
  .sliders { display: grid; grid-template-columns: minmax(90px, max-content) 1fr 48px; gap: 6px 12px; align-items: center; }
  .sliders .name { display: flex; align-items: center; gap: 8px; font-size: 14px; }
  .sliders output { text-align: right; font-variant-numeric: tabular-nums; font-size: 14px; }
  input[type="range"] { width: 100%; accent-color: var(--primary-color); }
  .swatch { display: inline-block; width: 10px; height: 10px; border-radius: 50%; flex: none; }
  .chart { margin-top: 12px; }
  .chart svg { width: 100%; height: auto; display: block; }
  .chart .grid { stroke: var(--divider-color); stroke-width: 1; }
  .chart .marker { stroke: var(--error-color, #db4437); stroke-width: 1.5; stroke-dasharray: 4 3; }
  .chart .marker.sun { stroke: var(--warning-color, #f9a825); }
  .chart .axis { fill: var(--secondary-text-color); font-size: 11px; }
  .legend { display: flex; flex-wrap: wrap; gap: 4px 16px; margin-top: 8px; font-size: 13px; }
  .legend-item { display: inline-flex; align-items: center; gap: 6px; }
  .table-wrap { overflow-x: auto; }
  table { border-collapse: collapse; width: 100%; }
  th, td { padding: 6px 8px; text-align: left; white-space: nowrap; }
  th { font-weight: 500; font-size: 13px; color: var(--secondary-text-color); border-bottom: 1px solid var(--divider-color); }
  th .swatch { margin-right: 6px; }
  .actions { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; margin-top: 20px; }
  .actions .spacer { flex: 1; }
  button.primary, button.secondary {
    font: inherit; font-size: 14px; font-weight: 500; padding: 8px 18px;
    border-radius: 8px; cursor: pointer;
  }
  button.primary { background: var(--primary-color); color: var(--text-primary-color, #fff); border: none; }
  button.secondary { background: none; color: var(--primary-color); border: 1px solid var(--divider-color); }
  button:disabled { opacity: 0.5; cursor: default; }
  button.remove {
    background: none; border: none; cursor: pointer; font-size: 18px; line-height: 1;
    color: var(--secondary-text-color); padding: 4px 8px;
  }
  button.remove:disabled { opacity: 0.3; }
  .check { display: inline-flex; align-items: center; gap: 8px; font-size: 14px; cursor: pointer; }
  .check input { width: 18px; height: 18px; accent-color: var(--primary-color); }
  .message { padding: 10px 14px; border-radius: 8px; margin-bottom: 16px; font-size: 14px; }
  .message.error { background: rgba(219, 68, 55, 0.12); color: var(--error-color, #db4437); }
  .message.success { background: rgba(67, 160, 71, 0.14); color: var(--success-color, #43a047); }
  .note { font-size: 13px; color: var(--secondary-text-color); margin-top: 8px; }
  .status { display: flex; flex-wrap: wrap; gap: 4px 24px; padding: 12px 14px; border-radius: 8px;
    background: var(--secondary-background-color, rgba(127,127,127,0.08)); font-size: 14px; margin-bottom: 12px; }
  .status strong { font-weight: 500; }
  .status .on { color: var(--success-color, #43a047); }
  .status .err { color: var(--error-color, #db4437); flex-basis: 100%; }
  .fade-times { font-size: 13px; color: var(--secondary-text-color); margin-top: 6px; }
  input.offset { width: 80px; }
`;

class FluvalSchedulePanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._lights = null;
    this._selected = null;
    this._tab = "auto";
    this._drafts = {};
    this._busy = false;
    this._message = null;
    this._loadError = null;
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (this._menuButton) this._menuButton.hass = hass;
    if (first) {
      this._renderShell();
      this._load(false);
    }
  }

  set narrow(narrow) {
    this._narrow = narrow;
    if (this._menuButton) this._menuButton.narrow = narrow;
  }

  set panel(panel) {
    this._panel = panel;
  }

  // --- data -----------------------------------------------------------------

  async _load(refresh) {
    this._busy = true;
    this._renderContent();
    try {
      const result = await this._hass.callWS({ type: `${DOMAIN}/lights`, refresh });
      this._lights = result.lights;
      this._loadError = null;
      if (!this._lights.some((l) => l.entry_id === this._selected)) {
        this._selected = this._lights.length ? this._lights[0].entry_id : null;
      }
      this._drafts = {};
    } catch (err) {
      this._loadError = err.message || String(err);
    }
    this._busy = false;
    this._renderContent();
  }

  _light() {
    return (this._lights || []).find((l) => l.entry_id === this._selected);
  }

  _draft() {
    const light = this._light();
    if (!light || !light.loaded) return null;
    if (!this._drafts[light.entry_id]) {
      this._drafts[light.entry_id] = {
        auto: clone(light.auto),
        pro: clone(light.pro),
        sun: light.sun_sync ? clone(light.sun_sync.config) : null,
        activateAuto: true,
        activatePro: true,
      };
    }
    return this._drafts[light.entry_id];
  }

  _replaceLight(updated) {
    this._lights = this._lights.map((l) => (l.entry_id === updated.entry_id ? updated : l));
  }

  async _call(message, successText) {
    this._busy = true;
    this._message = null;
    this._renderContent();
    try {
      const updated = await this._hass.callWS(message);
      this._replaceLight(updated);
      delete this._drafts[updated.entry_id];
      this._message = { type: "success", text: successText };
    } catch (err) {
      this._message = { type: "error", text: err.message || String(err) };
    }
    this._busy = false;
    this._renderContent();
  }

  _saveAuto() {
    const draft = this._draft();
    this._call(
      { type: `${DOMAIN}/set_auto`, entry_id: this._selected, schedule: draft.auto, activate: draft.activateAuto },
      draft.activateAuto ? "Auto schedule saved to the light and Auto mode switched on." : "Auto schedule saved to the light."
    );
  }

  _savePro() {
    const draft = this._draft();
    this._call(
      { type: `${DOMAIN}/set_pro`, entry_id: this._selected, schedule: { points: draft.pro.points }, activate: draft.activatePro },
      draft.activatePro ? "Pro schedule saved to the light and Pro mode switched on." : "Pro schedule saved to the light."
    );
  }

  _saveSunSync() {
    this._call(
      { type: `${DOMAIN}/set_sun_sync`, entry_id: this._selected, config: this._draft().sun },
      "Sun sync is on. Today's schedule has been pushed to the light, and it will be updated every night."
    );
  }

  _setMode(mode) {
    this._call({ type: `${DOMAIN}/set_mode`, entry_id: this._selected, mode }, `Switched to ${MODE_LABELS[mode]} mode.`);
  }

  _addPoint() {
    const points = this._draft().pro.points;
    if (points.length >= PRO_MAX_POINTS) return;
    const taken = new Set(points.map((p) => toMinutes(p.time)));
    const latest = Math.max(...taken);
    let at = Math.min(latest + 60, DAY_MINUTES - 1);
    while (taken.has(at)) at = (at + 1) % DAY_MINUTES;
    const template = points.reduce((a, b) => (toMinutes(a.time) >= toMinutes(b.time) ? a : b));
    points.push({ time: fromMinutes(at), values: [...template.values] });
    this._renderContent();
  }

  _removePoint(index) {
    const points = this._draft().pro.points;
    if (points.length <= PRO_MIN_POINTS) return;
    points.splice(index, 1);
    this._renderContent();
  }

  _sortPoints() {
    this._draft().pro.points.sort((a, b) => toMinutes(a.time) - toMinutes(b.time));
    this._renderContent();
  }

  // Apply an input's value to the draft at its data-path, e.g.
  // "auto.day.2" or "pro.points.3.values.1".
  _applyInput(target) {
    const path = target.dataset.path.split(".");
    let value;
    if (target.type === "checkbox") value = target.checked;
    else if (target.type === "range" || target.type === "number") {
      const min = target.min === "" ? -Infinity : Number(target.min);
      const max = target.max === "" ? Infinity : Number(target.max);
      value = Math.max(min, Math.min(max, Math.round(Number(target.value) || 0)));
    } else value = target.value;
    let obj = this._draft();
    for (const key of path.slice(0, -1)) obj = obj[key];
    obj[path[path.length - 1]] = value;
    const output = this.shadowRoot.querySelector(`output[data-for="${target.dataset.path}"]`);
    if (output) output.textContent = `${value}%`;
  }

  // --- rendering --------------------------------------------------------------

  _renderShell() {
    this.shadowRoot.innerHTML = `
      <style>${STYLE}</style>
      <div class="toolbar">
        <ha-menu-button></ha-menu-button>
        <div class="title">Aquarium Light</div>
        <button class="icon" data-action="refresh" title="Re-read the light">Refresh</button>
      </div>
      <div class="content"></div>`;
    this._menuButton = this.shadowRoot.querySelector("ha-menu-button");
    this._menuButton.hass = this._hass;
    this._menuButton.narrow = this._narrow;
    this._content = this.shadowRoot.querySelector(".content");

    this.shadowRoot.addEventListener("click", (ev) => this._onClick(ev));
    this.shadowRoot.addEventListener("input", (ev) => {
      if (!ev.target.dataset.path) return;
      this._applyInput(ev.target);
      this._renderChart();
    });
    this.shadowRoot.addEventListener("change", (ev) => {
      if (ev.target.dataset.action === "select-light") {
        this._selected = ev.target.value;
        this._message = null;
        this._renderContent();
      } else if (ev.target.dataset.path) {
        this._applyInput(ev.target);
        // Show the clamped value once the user is done typing.
        if (ev.target.type === "number") {
          const path = ev.target.dataset.path.split(".");
          let obj = this._draft();
          for (const key of path) obj = obj[key];
          ev.target.value = obj;
        }
        this._renderChart();
      }
    });
  }

  _onClick(ev) {
    const el = ev.target.closest("[data-action]");
    if (!el || el.disabled) return;
    const action = el.dataset.action;
    if (action === "refresh") this._load(true);
    else if (action === "tab") {
      this._tab = el.dataset.tab;
      this._message = null;
      this._renderContent();
    } else if (action === "mode") this._setMode(el.dataset.mode);
    else if (action === "save-auto") this._saveAuto();
    else if (action === "save-pro") this._savePro();
    else if (action === "save-sun") this._saveSunSync();
    else if (action === "sun-off") this._setMode("auto");
    else if (action === "reset") {
      delete this._drafts[this._selected];
      this._message = null;
      this._renderContent();
    } else if (action === "add-point") this._addPoint();
    else if (action === "remove-point") this._removePoint(Number(el.dataset.index));
    else if (action === "sort-points") this._sortPoints();
  }

  _renderContent() {
    if (!this._content) return;
    if (this._lights === null) {
      this._content.innerHTML = this._loadError
        ? `<div class="message error">Could not load lights: ${escapeHtml(this._loadError)}</div>`
        : `<p class="muted">Loading…</p>`;
      return;
    }
    if (!this._lights.length) {
      this._content.innerHTML = `<div class="card"><p class="muted">No Fluval Smart lights are set up yet. Add one under Settings → Devices &amp; Services.</p></div>`;
      return;
    }
    const light = this._light();
    let html = "";
    if (this._message) {
      html += `<div class="message ${this._message.type}">${escapeHtml(this._message.text)}</div>`;
    }
    html += this._renderHeaderCard(light);
    if (!light.loaded) {
      html += `<div class="card"><p class="muted">This light isn't connected. Home Assistant keeps retrying in the background; make sure it's powered on and in Bluetooth range, then press Refresh.</p></div>`;
    } else {
      html += `<div class="card">
        <div class="tabs">
          <button data-action="tab" data-tab="auto" class="${this._tab === "auto" ? "active" : ""}">Auto schedule</button>
          <button data-action="tab" data-tab="pro" class="${this._tab === "pro" ? "active" : ""}">Pro schedule</button>
          <button data-action="tab" data-tab="sun" class="${this._tab === "sun" ? "active" : ""}">Sun sync</button>
        </div>
        ${this._tab === "auto" ? this._renderAuto(light) : this._tab === "pro" ? this._renderPro(light) : this._renderSun(light)}
      </div>`;
    }
    this._content.innerHTML = html;
    this._renderChart();
  }

  _renderHeaderCard(light) {
    const picker =
      this._lights.length > 1
        ? `<select data-action="select-light">${this._lights
            .map((l) => `<option value="${escapeHtml(l.entry_id)}" ${l.entry_id === light.entry_id ? "selected" : ""}>${escapeHtml(l.title)}</option>`)
            .join("")}</select>`
        : "";
    const modes = light.loaded
      ? `<div class="segmented" role="group" aria-label="Mode">${Object.entries(MODE_LABELS)
          .map(
            ([mode, label]) =>
              `<button data-action="mode" data-mode="${mode}" class="${light.mode === mode ? "active" : ""}" ${this._busy ? "disabled" : ""}>${label}</button>`
          )
          .join("")}</div>`
      : "";
    const subtitle = light.loaded ? `${escapeHtml(light.model)} · ${escapeHtml(light.address)}` : "Not connected";
    return `<div class="card">
      <div class="row spread">
        <div>
          <h2>${escapeHtml(light.title)}</h2>
          <p class="muted">${subtitle}</p>
        </div>
        <div class="row">${picker}${modes}</div>
      </div>
    </div>`;
  }

  _sliders(prefix, values, channels) {
    return `<div class="sliders">${channels
      .map(
        (name, c) => `
        <span class="name"><span class="swatch" style="background:${channelColor(name, c)}"></span>${escapeHtml(name)}</span>
        <input type="range" min="0" max="100" step="1" value="${values[c]}" data-path="${prefix}.${c}" aria-label="${escapeHtml(name)}">
        <output data-for="${prefix}.${c}">${values[c]}%</output>`
      )
      .join("")}</div>`;
  }

  _renderAuto(light) {
    const draft = this._draft();
    const a = draft.auto;
    const disabled = this._busy ? "disabled" : "";
    return `
      <p class="muted">The light fades from night to day brightness over the sunrise window, and back over the sunset window. ${SOURCE_LABELS[light.auto_source]}</p>
      <div class="chart" data-chart></div>
      <h3>Sunrise &amp; sunset</h3>
      <div class="grid2">
        <label class="field">Sunrise starts<input type="time" value="${a.sunrise_start}" data-path="auto.sunrise_start" required></label>
        <label class="field">Sunrise ends<input type="time" value="${a.sunrise_end}" data-path="auto.sunrise_end" required></label>
        <label class="field">Sunset starts<input type="time" value="${a.sunset_start}" data-path="auto.sunset_start" required></label>
        <label class="field">Sunset ends<input type="time" value="${a.sunset_end}" data-path="auto.sunset_end" required></label>
      </div>
      <h3>Day brightness</h3>
      ${this._sliders("auto.day", a.day, light.channels)}
      <h3>Night brightness</h3>
      ${this._sliders("auto.night", a.night, light.channels)}
      <h3>Daily turn-off</h3>
      <div class="row">
        <label class="check"><input type="checkbox" data-path="auto.turnoff_enabled" ${a.turnoff_enabled ? "checked" : ""}>Turn the light off at</label>
        <input type="time" value="${a.turnoff}" data-path="auto.turnoff" aria-label="Turn-off time">
      </div>
      ${light.auto_dynamic ? `<p class="note">A dynamic effect set up in the FluvalSmart app is attached to this schedule and will be kept.</p>` : ""}
      <div class="actions">
        <label class="check"><input type="checkbox" data-path="activateAuto" ${draft.activateAuto ? "checked" : ""}>Switch to Auto mode after saving</label>
        <span class="spacer"></span>
        <button class="secondary" data-action="reset" ${disabled}>Reset</button>
        <button class="primary" data-action="save-auto" ${disabled}>${this._busy ? "Saving…" : "Save to light"}</button>
      </div>`;
  }

  _renderPro(light) {
    const draft = this._draft();
    const points = draft.pro.points;
    const disabled = this._busy ? "disabled" : "";
    const header = light.channels
      .map((name, c) => `<th><span class="swatch" style="background:${channelColor(name, c)}"></span>${escapeHtml(name)}</th>`)
      .join("");
    const rows = points
      .map(
        (p, i) => `<tr>
          <td><input type="time" value="${p.time}" data-path="pro.points.${i}.time" aria-label="Point ${i + 1} time" required></td>
          ${p.values
            .map(
              (v, c) =>
                `<td><input type="number" min="0" max="100" step="1" value="${v}" data-path="pro.points.${i}.values.${c}" aria-label="Point ${i + 1} ${escapeHtml(light.channels[c])}"></td>`
            )
            .join("")}
          <td><button class="remove" data-action="remove-point" data-index="${i}" title="Remove point" ${points.length <= PRO_MIN_POINTS ? "disabled" : ""}>×</button></td>
        </tr>`
      )
      .join("");
    return `
      <p class="muted">Brightness (%) is interpolated between consecutive points, wrapping around midnight. ${SOURCE_LABELS[light.pro_source]}</p>
      <div class="chart" data-chart></div>
      <h3>Points (${points.length} of ${PRO_MIN_POINTS}–${PRO_MAX_POINTS})</h3>
      <div class="table-wrap"><table>
        <thead><tr><th>Time</th>${header}<th></th></tr></thead>
        <tbody>${rows}</tbody>
      </table></div>
      <div class="row" style="margin-top:8px">
        <button class="secondary" data-action="add-point" ${points.length >= PRO_MAX_POINTS ? "disabled" : ""}>Add point</button>
        <button class="secondary" data-action="sort-points">Sort by time</button>
      </div>
      ${light.pro_dynamic ? `<p class="note">A dynamic effect set up in the FluvalSmart app is attached to this schedule and will be kept.</p>` : ""}
      <div class="actions">
        <label class="check"><input type="checkbox" data-path="activatePro" ${draft.activatePro ? "checked" : ""}>Switch to Pro mode after saving</label>
        <span class="spacer"></span>
        <button class="secondary" data-action="reset" ${disabled}>Reset</button>
        <button class="primary" data-action="save-pro" ${disabled}>${this._busy ? "Saving…" : "Save to light"}</button>
      </div>`;
  }

  _renderSun(light) {
    const draft = this._draft();
    const s = draft.sun;
    const info = light.sun_sync;
    const disabled = this._busy ? "disabled" : "";
    const status = info.enabled
      ? `<span><strong class="on">Sun sync is on</strong></span>
         <span>Last pushed: ${escapeHtml(formatDateTime(info.last_push))}</span>
         <span>Next push: ${escapeHtml(formatDateTime(info.next_push))}</span>`
      : `<span><strong>Sun sync is off</strong></span>`;
    const error = info.last_error ? `<span class="err">${escapeHtml(info.last_error)}</span>` : "";
    const sunLine = info.sunrise
      ? `On ${escapeHtml(new Date(info.date + "T12:00").toLocaleDateString([], { weekday: "long", month: "short", day: "numeric" }))} the sun rises at <strong>${info.sunrise}</strong> and sets at <strong>${info.sunset}</strong> at your Home Assistant location.`
      : `The sun doesn't both rise and set on ${escapeHtml(info.date)} at your Home Assistant location, so there's nothing to follow.`;
    const offsetField = (label, path, value, min, max) =>
      `<label class="field">${label}<span class="row" style="gap:6px"><input class="offset" type="number" min="${min}" max="${max}" step="1" value="${value}" data-path="${path}"> min</span></label>`;
    return `
      <div class="status">${status}${error}</div>
      <p class="muted">Home Assistant works out each day's sunrise and sunset, applies your offsets, and pushes the result to the light as its Auto schedule every night. ${sunLine}</p>
      <div class="chart" data-chart></div>
      <div class="fade-times" data-fade-times></div>
      <h3>Sunrise fade</h3>
      <div class="grid2">
        ${offsetField("Starts, relative to sunrise", "sun.sunrise_offset", s.sunrise_offset, -360, 360)}
        ${offsetField("Lasts", "sun.sunrise_duration", s.sunrise_duration, 1, 240)}
      </div>
      <h3>Sunset fade</h3>
      <div class="grid2">
        ${offsetField("Starts, relative to sunset", "sun.sunset_offset", s.sunset_offset, -360, 360)}
        ${offsetField("Lasts", "sun.sunset_duration", s.sunset_duration, 1, 240)}
      </div>
      <p class="note">Negative offsets start the fade before the sun event: e.g. a sunset fade starting −60 min and lasting 60 min ends exactly at sunset.</p>
      <h3>Day brightness</h3>
      ${this._sliders("sun.day", s.day, light.channels)}
      <h3>Night brightness</h3>
      ${this._sliders("sun.night", s.night, light.channels)}
      <h3>Daily turn-off</h3>
      <div class="row">
        <label class="check"><input type="checkbox" data-path="sun.turnoff_enabled" ${s.turnoff_enabled ? "checked" : ""}>Turn the light off at</label>
        <input type="time" value="${s.turnoff}" data-path="sun.turnoff" aria-label="Turn-off time">
      </div>
      <h3>Nightly update</h3>
      <div class="row">
        <label class="field">Push the new day's schedule to the light at<input type="time" value="${s.push_time}" data-path="sun.push_time" required></label>
      </div>
      <p class="note">Pick a time the light is reachable and between sunset and sunrise. If the light can't be reached, Home Assistant retries every 10 minutes for 3 hours.</p>
      <div class="actions">
        <span class="spacer"></span>
        ${info.enabled ? `<button class="secondary" data-action="sun-off" ${disabled} title="Keeps the light in Auto mode with the last pushed schedule">Turn off sun sync</button>` : ""}
        <button class="secondary" data-action="reset" ${disabled}>Reset</button>
        <button class="primary" data-action="save-sun" ${disabled || (info.sunrise ? "" : "disabled")}>${this._busy ? "Saving…" : info.enabled ? "Save & push now" : "Turn on & push now"}</button>
      </div>`;
  }

  _renderChart() {
    const container = this.shadowRoot.querySelector("[data-chart]");
    const light = this._light();
    const draft = this._draft();
    if (!container || !light || !draft) return;
    const channels = light.channels;
    let curves;
    let markers = [];
    if (this._tab === "auto") {
      const a = draft.auto;
      curves = autoCurves(a, channels.length);
      if (a.turnoff_enabled && a.turnoff) markers = [{ at: toMinutes(a.turnoff), label: "Off" }];
    } else if (this._tab === "sun") {
      const info = light.sun_sync;
      if (!info || !info.sunrise) return;
      const a = sunSchedule(draft.sun, info.sunrise, info.sunset);
      curves = autoCurves(a, channels.length);
      markers = [
        { at: toMinutes(info.sunrise), label: "Sunrise", cls: "sun" },
        { at: toMinutes(info.sunset), label: "Sunset", cls: "sun" },
      ];
      if (a.turnoff_enabled && a.turnoff) markers.push({ at: toMinutes(a.turnoff), label: "Off" });
      const fades = this.shadowRoot.querySelector("[data-fade-times]");
      if (fades) {
        fades.textContent = `Sunrise fade ${a.sunrise_start}–${a.sunrise_end} · Sunset fade ${a.sunset_start}–${a.sunset_end}`;
      }
    } else {
      curves = proCurves(draft.pro.points, channels.length);
    }
    // A cleared or half-typed time can't be plotted; keep the last chart.
    if (curves.flat(2).some(Number.isNaN) || markers.some((m) => Number.isNaN(m.at))) return;
    container.innerHTML = chartSvg(curves, channels, markers);
  }
}

if (!customElements.get("fluval-schedule-panel")) {
  customElements.define("fluval-schedule-panel", FluvalSchedulePanel);
}

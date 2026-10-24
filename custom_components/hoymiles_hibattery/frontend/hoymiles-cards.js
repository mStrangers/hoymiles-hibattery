/*
 * Hoymiles HiBattery - cartes du tableau de bord
 * Livrées par l'intégration hoymiles_hibattery (chargées automatiquement).
 *
 *   type: custom:hoymiles-battery-card    # état de la batterie
 *   type: custom:hoymiles-flow-card       # flux d'énergie de la maison
 *   type: custom:hoymiles-daily-card      # bilan du jour
 *   type: custom:hoymiles-settings-card   # pilotage : mode, planning actif, mode Manuel
 *
 * Options communes (toutes facultatives) :
 *   entity: sensor.msa_..._planning_tou    # si plusieurs batteries
 *   title: Mon titre
 *   grid_power: sensor.xxx                 # puissance réseau (+ achat), sinon celle du compteur de la batterie
 *   entities: { bat_temp: sensor.xxx }     # remplace une entité (clé de l'attribut « entites »)
 */
const HM_DOMAIN = "hoymiles_hibattery";
const HM_CARDS_VERSION = "1.0.4";

const hmEsc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const hmNum = (v, digits = 0) =>
  Number(v).toLocaleString("fr-FR", { minimumFractionDigits: digits, maximumFractionDigits: digits });
const hmPower = (w) => (Math.abs(w) >= 1000 ? `${hmNum(w / 1000, 2)} kW` : `${hmNum(w)} W`);
const hmEnergy = (kwh) => `${hmNum(kwh, 2)} kWh`;
const HM_SLOT_COLORS = { 1: "#ff9800", 2: "#43a047", 4: "#1e88e5" };

const HM_BASE_STYLE = `
  ha-card { padding: 16px; }
  .title { display:flex; align-items:center; gap:8px; font-size:1.15em; font-weight:500; margin-bottom:12px; }
  .title .spacer { flex:1; }
  .chip { display:inline-flex; align-items:center; gap:4px; padding:2px 10px; border-radius:12px; font-size:.8em;
    font-weight:400; white-space:nowrap; background: rgba(127,127,127,.15); color: var(--secondary-text-color); }
  .chip ha-icon { --mdc-icon-size:16px; }
  .chip.red { background: rgba(219,68,55,.15); color: var(--error-color, #db4437); }
  .chip.green { background: rgba(67,160,71,.15); color: #43a047; }
  .chip.orange { background: rgba(255,152,0,.15); color: #ef6c00; }
  .chip.blue { background: rgba(30,136,229,.15); color: #1e88e5; }
  .muted { color: var(--secondary-text-color); }
  .clickable { cursor: pointer; }
  .msg { padding: 8px 10px; border-radius: 6px; margin-top: 8px; font-size: .9em; background: rgba(30,136,229,.12); }
  .msg.err { background: rgba(219,68,55,.12); color: var(--error-color, #db4437); }
`;

/** Base commune : trouve la batterie et ses entités via l'attribut « entites » du capteur Planning TOU. */
class HoymilesBaseCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._ids = {};
  }

  static getStubConfig() {
    return {};
  }

  setConfig(config) {
    this._config = config || {};
    this._sig = null;
  }

  getCardSize() {
    return 4;
  }

  /** Clés des entités dont l'état déclenche un nouveau rendu. */
  _keys() {
    return [];
  }

  set hass(hass) {
    this._hass = hass;
    const h = hass.states;
    this._planning = this._config.entity
      ? h[this._config.entity]
      : Object.values(h).find((st) => st.entity_id.startsWith("sensor.") && st.attributes.entites && st.attributes.entry_id);
    this._ids = (this._planning && this._planning.attributes.entites) || {};
    const watched = [this._planning && this._planning.entity_id, ...this._keys().map((k) => this._id(k))];
    const sig = JSON.stringify(watched.map((id) => (id && h[id] ? [h[id].state, h[id].last_updated] : null)));
    if (sig === this._sig) return;
    // ne pas redessiner pendant une saisie (curseur, liste déroulante)
    const focused = this.shadowRoot.activeElement;
    if (focused && ["INPUT", "SELECT"].includes(focused.tagName)) return;
    this._sig = sig;
    this._render();
  }

  _id(key) {
    const id =
      (this._config.entities || {})[key] || (key === "grid_power" && this._config.grid_power) || this._ids[key];
    return id && this._hass.states[id] ? id : undefined;
  }

  _st(key) {
    const id = this._id(key);
    return id ? this._hass.states[id] : undefined;
  }

  _num(key) {
    const st = this._st(key);
    const v = st ? parseFloat(st.state) : NaN;
    return Number.isFinite(v) ? v : null;
  }

  _moreInfo(key) {
    const entityId = this._id(key);
    if (entityId) this.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId }, bubbles: true, composed: true }));
  }

  _show(html, style = "") {
    this.shadowRoot.innerHTML = `<style>${HM_BASE_STYLE}${style}</style><ha-card>${html}</ha-card>`;
    this.shadowRoot.querySelectorAll("[data-info]").forEach((el) =>
      el.addEventListener("click", () => this._moreInfo(el.dataset.info))
    );
  }

  _render() {
    if (!this._planning) {
      this._show(`<div class="muted">Aucune batterie <b>Hoymiles HiBattery</b> trouvée (intégration non installée ?).</div>`);
      return;
    }
    this._renderCard();
  }
}

// ------------------------------------------------------------- état batterie
class HoymilesBatteryCard extends HoymilesBaseCard {
  _keys() {
    return ["soc", "bat_p", "bat_sts", "energy_available", "bat_temp", "heat", "mode", "plan", "plan_issue", "ems_mode"];
  }

  _renderCard() {
    const soc = this._num("soc");
    const p = this._num("bat_p");
    const pct = soc === null ? 0 : Math.max(0, Math.min(100, soc));
    const color = pct < 15 ? "var(--error-color, #db4437)" : pct < 40 ? "#ff9800" : "#43a047";
    const r = 52;
    const circ = 2 * Math.PI * r;
    let flow = { text: "Veille", icon: "mdi:battery", cls: "" };
    if (p !== null && p > 10) flow = { text: `Décharge ${hmPower(p)}`, icon: "mdi:battery-arrow-down", cls: "orange" };
    else if (p !== null && p < -10) flow = { text: `Charge ${hmPower(-p)}`, icon: "mdi:battery-arrow-up", cls: "green" };

    const a = this._planning.attributes;
    const slot = a.creneau_actuel;
    const mode = this._st("mode");
    const issue = this._st("plan_issue");
    const heat = this._st("heat");
    const temp = this._num("bat_temp");
    const energy = this._num("energy_available");
    const chips = [
      mode && `<span class="chip blue clickable" data-info="mode"><ha-icon icon="mdi:home-battery"></ha-icon>${hmEsc(mode.state)}</span>`,
      issue && issue.state === "on" &&
        `<span class="chip red clickable" data-info="plan_issue"><ha-icon icon="mdi:calendar-alert"></ha-icon>Planning non respecté</span>`,
      heat && heat.state === "on" && `<span class="chip orange"><ha-icon icon="mdi:heat-wave"></ha-icon>Chauffage</span>`,
    ].filter(Boolean).join("");
    const slotLine =
      mode && mode.state === "Planning" && slot
        ? `<div class="slot"><i style="background:${HM_SLOT_COLORS[slot.mode] || "#999"}"></i>
             ${hmEsc(slot.debut)}–${hmEsc(slot.fin)} · ${hmEsc(this._planning.state.replace(/^\S+\s/, ""))}
             <span class="muted">(${hmEsc(a.planning)})</span></div>`
        : "";

    this._show(
      `<div class="title"><ha-icon icon="mdi:home-battery"></ha-icon>${hmEsc(this._config.title || "Batterie")}
         <span class="spacer"></span>${chips}</div>
       <div class="body">
         <div class="ring clickable" data-info="soc">
           <svg viewBox="0 0 120 120">
             <circle cx="60" cy="60" r="${r}" class="track"></circle>
             <circle cx="60" cy="60" r="${r}" class="bar" style="stroke:${color};stroke-dasharray:${(circ * pct) / 100} ${circ}"></circle>
           </svg>
           <div class="soc"><b>${soc === null ? "–" : hmNum(soc)}</b><span>%</span></div>
         </div>
         <div class="info">
           <div class="state ${flow.cls} clickable" data-info="bat_p"><ha-icon icon="${flow.icon}"></ha-icon>${flow.text}</div>
           ${energy !== null ? `<div class="clickable" data-info="energy_available"><span class="muted">Disponible</span> ${hmEnergy(energy)}</div>` : ""}
           ${temp !== null ? `<div class="clickable" data-info="bat_temp"><span class="muted">Température</span> ${hmNum(temp, 1)} °C</div>` : ""}
         </div>
       </div>
       ${slotLine}`,
      `.body { display:flex; align-items:center; gap:20px; }
       .ring { position:relative; width:120px; height:120px; flex-shrink:0; }
       .ring svg { transform: rotate(-90deg); width:100%; height:100%; }
       .ring circle { fill:none; stroke-width:10; }
       .ring .track { stroke: var(--divider-color, #ddd); }
       .ring .bar { stroke-linecap: round; transition: stroke-dasharray .6s; }
       .soc { position:absolute; inset:0; display:flex; align-items:baseline; justify-content:center; padding-top:42px; }
       .soc b { font-size:2em; font-weight:500; } .soc span { color: var(--secondary-text-color); margin-left:2px; }
       .info { display:flex; flex-direction:column; gap:6px; font-size:.95em; }
       .state { display:flex; align-items:center; gap:6px; font-size:1.2em; font-weight:500; }
       .state.orange { color:#ef6c00; } .state.green { color:#43a047; }
       .slot { margin-top:12px; padding-top:10px; border-top:1px solid var(--divider-color); font-size:.9em; display:flex; align-items:center; gap:6px; flex-wrap:wrap; }
       .slot i { width:10px; height:10px; border-radius:2px; display:inline-block; }`
    );
  }
}

// ------------------------------------------------------------- flux d'énergie
class HoymilesFlowCard extends HoymilesBaseCard {
  _keys() {
    return ["pv_total_p", "grid_power", "sys_grid_p", "bat_p", "soc", "sys_load_p"];
  }

  getCardSize() {
    return 5;
  }

  _renderCard() {
    const pv = this._num("pv_total_p");
    const gridKey = this._id("grid_power") ? "grid_power" : "sys_grid_p";
    const grid = this._num(gridKey);
    const bat = this._num("bat_p");
    const soc = this._num("soc");
    // maison : mesurée par le compteur de la batterie, sinon bilan approché
    let home = this._num("sys_load_p");
    if (home === null && grid !== null) home = Math.max(0, (pv || 0) + grid + (bat || 0));

    // sens des flux vis-à-vis de la maison : « in » = vers le centre, « out » = depuis le centre
    const dir = (v, inWhenPositive = true) =>
      v === null || Math.abs(v) < 10 ? "idle" : (v > 0) === inWhenPositive ? "in" : "out";
    const nodes = [
      { key: "pv_total_p", x: 50, y: 15, icon: "mdi:solar-power-variant", label: "Solaire", color: "#ff9800",
        value: pv, dir: dir(pv), sub: "" },
      { key: gridKey, x: 13, y: 50, icon: "mdi:transmission-tower", label: "Réseau", color: "#7e57c2",
        value: grid === null ? null : Math.abs(grid), dir: dir(grid),
        sub: grid === null || Math.abs(grid) < 10 ? "" : grid > 0 ? "achat" : "injection" },
      { key: "sys_load_p", x: 87, y: 50, icon: "mdi:home", label: "Maison", color: "#26a69a",
        value: home, dir: dir(home, false), sub: "" },
      { key: "bat_p", x: 50, y: 80, icon: "mdi:battery", label: "Batterie", color: "#43a047",
        value: bat === null ? null : Math.abs(bat), dir: dir(bat),
        sub: [soc !== null ? `${hmNum(soc)} %` : "", bat === null || Math.abs(bat) < 10 ? "" : bat > 0 ? "décharge" : "charge"]
          .filter(Boolean).join(" · ") },
    ];
    const lines = nodes
      .map((n) => `<line x1="${n.x}" y1="${n.y}" x2="50" y2="50" class="ln ${n.dir}" style="stroke:${n.color}"></line>`)
      .join("");
    const html = nodes
      .map(
        (n) => `<div class="node ${n.value === null ? "na" : ""} clickable" style="left:${n.x}%;top:${n.y}%" data-info="${n.key}">
          <div class="circle" style="border-color:${n.color}"><ha-icon icon="${n.icon}" style="color:${n.color}"></ha-icon>
            <b>${n.value === null ? "–" : hmPower(n.value)}</b></div>
          <div class="lbl">${n.label}${n.sub ? `<br><span class="muted">${n.sub}</span>` : ""}</div></div>`
      )
      .join("");
    this._show(
      `${this._config.title ? `<div class="title"><ha-icon icon="mdi:transit-connection-variant"></ha-icon>${hmEsc(this._config.title)}</div>` : ""}
       <div class="flow"><svg viewBox="0 0 100 100" preserveAspectRatio="none">${lines}</svg>${html}</div>`,
      `.flow { position:relative; width:100%; aspect-ratio: 1.25; max-height: 340px; margin: 0 auto; }
       .flow svg { position:absolute; inset:0; width:100%; height:100%; overflow:visible; }
       .ln { vector-effect: non-scaling-stroke; stroke-width:2.5; opacity:.9; }
       .ln.idle { opacity:.2; }
       .ln.in, .ln.out { stroke-dasharray: 6 6; animation: hm-flow 1s linear infinite; }
       /* les lignes vont du nœud (x1) vers le centre (x2) */
       .ln.in { animation-direction: normal; } .ln.out { animation-direction: reverse; }
       @keyframes hm-flow { from { stroke-dashoffset: 12; } to { stroke-dashoffset: 0; } }
       /* le cercle (74 px + bordures = 80 px) est centré sur le point, le libellé dessous */
       .node { position:absolute; transform: translate(-50%, -40px); display:flex; flex-direction:column; align-items:center; text-align:center; }
       .node.na { opacity:.5; }
       .circle { width:74px; height:74px; border-radius:50%; border:3px solid; background: var(--card-background-color, #fff);
         display:flex; flex-direction:column; align-items:center; justify-content:center; gap:2px; font-size:.85em; }
       .circle ha-icon { --mdc-icon-size: 22px; }
       .lbl { font-size:.8em; margin-top:4px; line-height:1.2; background: var(--card-background-color, #fff); padding: 0 4px; border-radius: 4px; }
       @media (prefers-reduced-motion: reduce) { .ln.in, .ln.out { animation: none; } }`
    );
  }
}

// ------------------------------------------------------------- bilan du jour
class HoymilesDailyCard extends HoymilesBaseCard {
  _keys() {
    return ["pv_today", "grid_import_today", "grid_export_today", "home_today", "out_today", "in_today",
      "charge_today", "discharge_today"];
  }

  _renderCard() {
    const v = (k) => this._num(k);
    const pv = v("pv_today");
    const imp = v("grid_import_today");
    const exp = v("grid_export_today");
    const home = v("home_today");
    const rows = [
      ["pv_today", "Production PV", "mdi:solar-power", "#ff9800"],
      ["home_today", "Consommation maison", "mdi:home-lightning-bolt", "#26a69a"],
      ["grid_import_today", "Achat réseau", "mdi:transmission-tower-import", "#7e57c2"],
      ["grid_export_today", "Injection", "mdi:transmission-tower-export", "#90a4ae"],
      ["charge_today", "Batterie chargée", "mdi:battery-arrow-up", "#43a047"],
      ["discharge_today", "Batterie déchargée", "mdi:battery-arrow-down", "#1e88e5"],
    ].filter(([k]) => v(k) !== null);
    const max = Math.max(0.01, ...rows.map(([k]) => v(k)));
    // part de la production PV consommée sur place, et part de la consommation couverte sans le réseau
    const ratios = [
      pv !== null && exp !== null && pv > 0.05 && ["Autoconsommation", Math.max(0, Math.min(1, 1 - exp / pv))],
      home !== null && imp !== null && home > 0.05 && ["Autonomie", Math.max(0, Math.min(1, 1 - imp / home))],
    ].filter(Boolean);
    this._show(
      `<div class="title"><ha-icon icon="mdi:calendar-today"></ha-icon>${hmEsc(this._config.title || "Aujourd'hui")}</div>
       ${ratios.length ? `<div class="ratios">${ratios.map(([label, r]) => `
         <div class="ratio"><b>${hmNum(r * 100)} %</b><span class="muted">${label}</span>
           <div class="gauge"><i style="width:${r * 100}%"></i></div></div>`).join("")}</div>` : ""}
       <div class="rows">${rows.map(([k, label, icon, color]) => `
         <div class="row clickable" data-info="${k}"><ha-icon icon="${icon}" style="color:${color}"></ha-icon>
           <span class="lbl">${label}</span>
           <span class="bar"><i style="width:${(v(k) / max) * 100}%;background:${color}"></i></span>
           <b>${hmEnergy(v(k))}</b></div>`).join("")}</div>
       ${rows.length ? "" : `<div class="muted">Pas encore de données aujourd'hui.</div>`}`,
      `.ratios { display:flex; gap:16px; margin-bottom:14px; }
       .ratio { flex:1; display:flex; flex-direction:column; gap:2px; }
       .ratio b { font-size:1.6em; font-weight:500; }
       .gauge { height:6px; border-radius:3px; background: var(--divider-color, #ddd); overflow:hidden; margin-top:4px; }
       .gauge i { display:block; height:100%; background:#43a047; }
       .rows { display:flex; flex-direction:column; gap:8px; }
       .row { display:grid; grid-template-columns: 24px minmax(110px, 1fr) 2fr auto; align-items:center; gap:8px; font-size:.92em; }
       .row ha-icon { --mdc-icon-size: 20px; }
       .row b { font-weight:500; min-width:72px; text-align:right; }
       .bar { height:8px; border-radius:4px; background: rgba(127,127,127,.12); overflow:hidden; }
       .bar i { display:block; height:100%; border-radius:4px; }`
    );
  }
}

// ------------------------------------------------------------- pilotage
class HoymilesSettingsCard extends HoymilesBaseCard {
  _keys() {
    return ["mode", "plan", "plan_issue", "manual_setpoint", "discharge_max", "soc_min", "setpoint", "ems_mode"];
  }

  getCardSize() {
    return 5;
  }

  async _call(domain, service, data, okMsg) {
    this._message = null;
    try {
      await this._hass.callService(domain, service, data);
      if (okMsg) this.dispatchEvent(new CustomEvent("hass-notification", { detail: { message: okMsg }, bubbles: true, composed: true }));
    } catch (err) {
      this._message = { error: true, text: (err && err.message) || String(err) };
      this._render();
    }
  }

  _renderCard() {
    const mode = this._st("mode");
    const plan = this._st("plan");
    const a = this._planning.attributes;
    const current = mode ? mode.state : "";
    const modes = (mode && mode.attributes.options) || [];
    const icons = { Planning: "mdi:calendar-clock", Manuel: "mdi:hand-back-right", "Auto (Hoymiles)": "mdi:auto-mode" };
    const seg = modes
      .map((m) => `<button class="seg ${m === current ? "on" : ""}" data-mode="${hmEsc(m)}"><ha-icon icon="${icons[m] || "mdi:circle"}"></ha-icon>${hmEsc(m)}</button>`)
      .join("");

    let body = "";
    if (current === "Planning") {
      const issue = this._st("plan_issue");
      const options = (plan && plan.attributes.options) || [];
      const slot = a.creneau_actuel;
      body = `
        <label class="line">Planning actif
          <select class="plan">${options.map((o) => `<option ${o === (plan && plan.state) ? "selected" : ""}>${hmEsc(o)}</option>`).join("")}</select>
        </label>
        <div class="line">
          ${slot ? `<span><i class="sq" style="background:${HM_SLOT_COLORS[slot.mode] || "#999"}"></i>${hmEsc(slot.debut)}–${hmEsc(slot.fin)} ${hmEsc(this._planning.state.replace(/^\S+\s/, ""))}</span>` : `<span class="muted">Aucun créneau en cours</span>`}
          ${issue && issue.state === "on"
            ? `<span class="chip red clickable" data-info="plan_issue"><ha-icon icon="mdi:calendar-alert"></ha-icon>Non respecté</span>`
            : `<span class="chip green"><ha-icon icon="mdi:calendar-check"></ha-icon>Respecté</span>`}
        </div>
        ${a.dernier_resultat ? `<div class="muted small">${hmEsc(a.dernier_resultat)}</div>` : ""}
        <div class="actions">
          <button class="btn" data-act="read"><ha-icon icon="mdi:calendar-search"></ha-icon>Lire la batterie</button>
          <button class="btn primary" data-act="send"><ha-icon icon="mdi:calendar-arrow-right"></ha-icon>Renvoyer</button>
        </div>`;
    } else if (current === "Manuel") {
      const sp = this._st("manual_setpoint");
      const max = this._num("discharge_max") ?? (sp ? sp.attributes.max : 2500);
      const val = this._num("manual_setpoint") ?? 0;
      const sent = this._num("setpoint");
      body = `
        <div class="line"><span>Consigne de décharge</span><b class="spv">${hmPower(Math.min(val, max))}</b></div>
        <input type="range" class="sp" min="0" max="${max}" step="10" value="${Math.min(val, max)}">
        <div class="grid2">
          <label>Décharge max <span><input type="number" class="nb" data-key="discharge_max" min="0" max="2500" step="50" value="${this._num("discharge_max") ?? ""}"> W</span></label>
          <label>SOC mini <span><input type="number" class="nb" data-key="soc_min" min="0" max="100" step="1" value="${this._num("soc_min") ?? ""}"> %</span></label>
        </div>
        ${sent !== null ? `<div class="muted small clickable" data-info="setpoint">Consigne envoyée à la batterie : ${hmPower(sent)}</div>` : ""}`;
    } else if (current) {
      body = `<div class="muted">La batterie fonctionne seule (mode automatique Hoymiles).</div>`;
    }

    this._show(
      `<div class="title"><ha-icon icon="mdi:tune"></ha-icon>${hmEsc(this._config.title || "Pilotage de la batterie")}</div>
       <div class="segs">${seg}</div>
       <div class="body">${body}</div>
       ${this._message ? `<div class="msg ${this._message.error ? "err" : ""}">${hmEsc(this._message.text)}</div>` : ""}`,
      `.segs { display:flex; border:1px solid var(--divider-color, #ccc); border-radius:20px; overflow:hidden; margin-bottom:14px; }
       .seg { flex:1; display:flex; align-items:center; justify-content:center; gap:6px; padding:8px 6px; border:none;
         background:none; color: var(--primary-text-color); font: inherit; font-size:.9em; cursor:pointer; }
       .seg + .seg { border-left:1px solid var(--divider-color, #ccc); }
       .seg.on { background: var(--primary-color); color: var(--text-primary-color, #fff); }
       .seg ha-icon { --mdc-icon-size:18px; }
       .body { display:flex; flex-direction:column; gap:10px; }
       .line { display:flex; align-items:center; justify-content:space-between; gap:8px; flex-wrap:wrap; }
       .sq { display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:6px; }
       .small { font-size:.85em; }
       select, input[type=number] { font: inherit; color: var(--primary-text-color); background: var(--card-background-color, #fff);
         border:1px solid var(--divider-color, #ccc); border-radius:6px; padding:4px 6px; }
       select.plan { min-width: 160px; }
       input[type=number] { width: 64px; text-align:right; }
       input[type=range] { width:100%; accent-color: var(--primary-color); }
       .grid2 { display:flex; gap:16px; flex-wrap:wrap; }
       .grid2 label { display:flex; align-items:center; gap:6px; color: var(--secondary-text-color); font-size:.9em; }
       .actions { display:flex; justify-content:flex-end; gap:8px; flex-wrap:wrap; }
       .btn { display:inline-flex; align-items:center; gap:6px; border-radius:18px; padding:6px 14px; font: inherit; cursor:pointer;
         border:1px solid var(--primary-color); background:none; color: var(--primary-color); }
       .btn.primary { background: var(--primary-color); color: var(--text-primary-color, #fff); }
       .btn ha-icon { --mdc-icon-size:18px; }`
    );
    this._bind();
  }

  _bind() {
    const r = this.shadowRoot;
    const entryId = this._planning.attributes.entry_id;
    r.querySelectorAll("button[data-mode]").forEach((el) =>
      el.addEventListener("click", () => {
        if (el.dataset.mode !== this._st("mode").state)
          this._call("select", "select_option", { entity_id: this._id("mode"), option: el.dataset.mode });
      })
    );
    const plan = r.querySelector("select.plan");
    if (plan)
      plan.addEventListener("change", (e) => {
        plan.blur();
        this._call("select", "select_option", { entity_id: this._id("plan"), option: e.target.value },
          `Planning « ${e.target.value} » activé`);
      });
    const sp = r.querySelector("input.sp");
    if (sp) {
      sp.addEventListener("input", () => (r.querySelector(".spv").textContent = hmPower(+sp.value)));
      sp.addEventListener("change", () => {
        sp.blur();
        this._call("number", "set_value", { entity_id: this._id("manual_setpoint"), value: +sp.value });
      });
    }
    r.querySelectorAll("input.nb").forEach((el) =>
      el.addEventListener("change", () => {
        el.blur();
        if (el.value !== "") this._call("number", "set_value", { entity_id: this._id(el.dataset.key), value: +el.value });
      })
    );
    const acts = {
      read: () => this._call(HM_DOMAIN, "read_plan", { entry_id: entryId, day: "Mon", notify: true },
        "Planning de la batterie : voir les notifications"),
      send: () => this._call(HM_DOMAIN, "send_plan", { entry_id: entryId }, "Planning renvoyé à la batterie"),
    };
    r.querySelectorAll("button[data-act]").forEach((el) => el.addEventListener("click", () => acts[el.dataset.act]()));
  }
}

// Home Assistant remplace window.customElements au démarrage de son interface : on attend qu'elle
// soit prête avant de définir les cartes (voir hoymiles-tou-card.js).
const HM_CARDS = [
  ["hoymiles-battery-card", HoymilesBatteryCard, "Hoymiles - état batterie", "SOC, charge/décharge, mode et créneau en cours"],
  ["hoymiles-flow-card", HoymilesFlowCard, "Hoymiles - flux d'énergie", "Solaire, réseau, batterie et maison en temps réel"],
  ["hoymiles-daily-card", HoymilesDailyCard, "Hoymiles - bilan du jour", "Production, consommation, réseau, autoconsommation et autonomie"],
  ["hoymiles-settings-card", HoymilesSettingsCard, "Hoymiles - pilotage", "Mode de pilotage, planning actif et réglages du mode Manuel"],
];
customElements.whenDefined("home-assistant").then(() => {
  window.customCards = window.customCards || [];
  for (const [tag, cls, name, description] of HM_CARDS) {
    if (customElements.get(tag)) continue;
    customElements.define(tag, cls);
    window.customCards.push({ type: tag, name, description, preview: true });
  }
  console.info(`%c HOYMILES-CARDS %c ${HM_CARDS_VERSION} `, "background:#1e88e5;color:#fff", "");
});

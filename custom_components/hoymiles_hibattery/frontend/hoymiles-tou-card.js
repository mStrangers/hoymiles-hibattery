/*
 * Hoymiles HiBattery - carte d'édition des plannings TOU
 * Livrée par l'intégration hoymiles_hibattery (chargée automatiquement).
 *
 *   type: custom:hoymiles-tou-card
 *   entity: sensor.msa_280526231099_planning_tou
 */
const DOMAIN = "hoymiles_hibattery";
const CARD_VERSION = "1.0.1";

const MODES = {
  1: { name: "Charge forcée", short: "Charge forcée", color: "#ff9800", icon: "mdi:battery-charging-high" },
  2: { name: "Recharge prioritaire PV", short: "Recharge PV", color: "#43a047", icon: "mdi:solar-power" },
  4: { name: "Autoconsommation", short: "Autoconso", color: "#1e88e5", icon: "mdi:home-lightning-bolt" },
};

const toQ = (hhmm) => {
  const [h, m] = String(hhmm).split(":").map(Number);
  return h * 4 + Math.floor(m / 15);
};
const toHHMM = (q) => `${String(Math.floor(q / 4)).padStart(2, "0")}:${String((q % 4) * 15).padStart(2, "0")}`;
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const clone = (o) => JSON.parse(JSON.stringify(o));

class HoymilesTouCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._editName = null; // planning affiché
    this._pendingName = null; // planning créé/renommé, pas encore visible dans l'état
    this._slots = null; // copie en cours d'édition
    this._dirty = false;
    this._busy = false;
    this._message = null;
  }

  static getStubConfig(hass) {
    const ent = Object.keys(hass.states).find(
      (e) => e.startsWith("sensor.") && hass.states[e].attributes.plans && hass.states[e].attributes.entry_id
    );
    return { entity: ent || "sensor.msa_280526231099_planning_tou" };
  }

  setConfig(config) {
    if (!config || !config.entity) throw new Error("Indiquez l'entité « Planning TOU » (entity)");
    this._config = config;
  }

  getCardSize() {
    return 8;
  }

  set hass(hass) {
    this._hass = hass;
    const st = hass.states[this._config.entity];
    const sig = st ? JSON.stringify([st.state, st.attributes]) : "none";
    if (sig === this._lastSig && this.shadowRoot.innerHTML) return;
    // ne pas redessiner pendant la saisie (évite de perdre le champ en cours)
    const focused = this.shadowRoot.activeElement;
    if (focused && ["INPUT", "SELECT"].includes(focused.tagName)) return;
    this._lastSig = sig;
    if (!this._dirty) this._syncFromState();
    this._render();
  }

  // ------------------------------------------------------------------ état
  get _attrs() {
    const st = this._hass && this._hass.states[this._config.entity];
    return st ? st.attributes : null;
  }

  _syncFromState() {
    const a = this._attrs;
    if (!a || !a.plans) return;
    const names = Object.keys(a.plans);
    if (this._pendingName) {
      // l'état peut arriver après la réponse du service : on attend qu'il contienne le planning
      if (!names.includes(this._pendingName)) return;
      this._pendingName = null;
    }
    if (!this._editName || !names.includes(this._editName)) this._editName = a.planning || names[0];
    this._slots = clone(a.plans[this._editName] || []);
  }

  _validate() {
    const s = this._slots || [];
    if (!s.length) return "Le planning doit contenir au moins un créneau.";
    if (s.length > 12) return "La batterie accepte au maximum 12 créneaux.";
    for (let i = 0; i < s.length; i++) {
      const x = s[i];
      if (toQ(x.fin) <= toQ(x.debut)) return `Créneau ${i + 1} : la fin doit être après le début.`;
      const rng = (v, lo, hi) => Number.isFinite(+v) && +v >= lo && +v <= hi;
      if (!rng(x.sh, 10, 100) || !rng(x.sl, 10, 100)) return `Créneau ${i + 1} : SOC entre 10 et 100 %.`;
      if (!rng(x.pc, 100, 1000) || !rng(x.pd, 100, 1000)) return `Créneau ${i + 1} : puissance entre 100 et 1000 W.`;
    }
    return null;
  }

  // --------------------------------------------------------------- actions
  async _call(service, data, okMsg) {
    this._busy = true;
    this._message = null;
    this._render();
    try {
      const entry_id = this._attrs && this._attrs.entry_id;
      await this._hass.callService(DOMAIN, service, { entry_id, ...data });
      if (okMsg) this._toast(okMsg);
      return true;
    } catch (err) {
      this._message = { error: true, text: err && err.message ? err.message : String(err) };
      return false;
    } finally {
      this._busy = false;
      this._render();
    }
  }

  _toast(message) {
    this.dispatchEvent(new CustomEvent("hass-notification", { detail: { message }, bubbles: true, composed: true }));
  }

  _setDirty() {
    this._dirty = true;
    this._message = null;
    this._render();
  }

  _changeFin(i, q) {
    const s = this._slots;
    s[i].fin = toHHMM(q);
    if (s[i + 1]) s[i + 1].debut = toHHMM(q);
    this._setDirty();
  }

  _split(i) {
    const s = this._slots;
    if (s.length >= 12) return;
    const a = toQ(s[i].debut);
    const b = toQ(s[i].fin);
    if (b - a < 2) return;
    const mid = a + Math.floor((b - a) / 2);
    const copy = clone(s[i]);
    s[i].fin = toHHMM(mid);
    copy.debut = toHHMM(mid);
    s.splice(i + 1, 0, copy);
    this._setDirty();
  }

  _remove(i) {
    const s = this._slots;
    if (s.length <= 1) return;
    if (i === 0) s[1].debut = "00:00";
    else s[i - 1].fin = s[i].fin;
    s.splice(i, 1);
    this._setDirty();
  }

  async _save() {
    const err = this._validate();
    if (err) {
      this._message = { error: true, text: err };
      return this._render();
    }
    const active = this._attrs.planning === this._editName && this._attrs.mode_pilotage === "Planning";
    if (await this._call("save_plan", { name: this._editName, slots: this._slots },
      active ? "Planning enregistré et envoyé à la batterie" : "Planning enregistré")) {
      this._dirty = false;
    }
  }

  async _saveAs() {
    const err = this._validate();
    if (err) {
      this._message = { error: true, text: err };
      return this._render();
    }
    const name = prompt("Nom du nouveau planning :", `${this._editName} (copie)`);
    if (!name) return;
    if (await this._call("save_plan", { name, slots: this._slots }, `Planning « ${name} » créé`)) {
      this._dirty = false;
      this._editName = this._pendingName = name;
      this._syncFromState();
      this._render();
    }
  }

  async _rename() {
    const name = prompt("Nouveau nom :", this._editName);
    if (!name || name === this._editName) return;
    const slots = this._dirty ? this._slots : this._attrs.plans[this._editName];
    if (await this._call("save_plan", { name, old_name: this._editName, slots }, "Planning renommé")) {
      this._dirty = false;
      this._editName = this._pendingName = name;
      this._syncFromState();
      this._render();
    }
  }

  async _new() {
    const name = prompt("Nom du nouveau planning :");
    if (!name) return;
    const slots = [{ mode: 4, debut: "00:00", fin: "24:00", sh: 100, sl: 10, pc: 800, pd: 200 }];
    if (await this._call("save_plan", { name, slots }, `Planning « ${name} » créé`)) {
      this._dirty = false;
      this._editName = this._pendingName = name;
      this._slots = clone(slots);
      this._syncFromState();
      this._render();
    }
  }

  async _delete() {
    if (!confirm(`Supprimer le planning « ${this._editName} » ?`)) return;
    if (await this._call("delete_plan", { name: this._editName }, "Planning supprimé")) {
      this._dirty = false;
      this._editName = null;
      this._syncFromState();
    }
  }

  async _activate() {
    if (this._dirty && !confirm("Des modifications ne sont pas enregistrées. Activer quand même la version enregistrée ?")) return;
    await this._call("select_plan", { name: this._editName }, `Planning « ${this._editName} » activé`);
  }

  _cancel() {
    this._dirty = false;
    this._message = null;
    this._syncFromState();
    this._render();
  }

  _switchPlan(name) {
    if (this._dirty && !confirm("Abandonner les modifications non enregistrées ?")) return this._render();
    this._dirty = false;
    this._message = null;
    this._editName = name;
    this._syncFromState();
    this._render();
  }

  // ---------------------------------------------------------------- rendu
  _timeline(slots) {
    const now = new Date();
    const nowPct = ((now.getHours() * 60 + now.getMinutes()) / 1440) * 100;
    const segs = slots
      .map((s) => {
        const a = (toQ(s.debut) / 96) * 100;
        const w = ((toQ(s.fin) - toQ(s.debut)) / 96) * 100;
        const m = MODES[s.mode] || { color: "#999", short: "?" };
        return `<div class="seg" style="left:${a}%;width:${w}%;background:${m.color}" title="${s.debut}-${s.fin} ${m.name}"></div>`;
      })
      .join("");
    const ticks = [0, 3, 6, 9, 12, 15, 18, 21, 24]
      .map((h) => `<span style="left:${(h / 24) * 100}%">${h}h</span>`)
      .join("");
    return `<div class="timeline">${segs}<div class="now" style="left:${nowPct}%"></div></div><div class="ticks">${ticks}</div>`;
  }

  _timeOptions(min, max, value) {
    let out = "";
    for (let q = min; q <= max; q++) out += `<option value="${q}" ${q === value ? "selected" : ""}>${toHHMM(q)}</option>`;
    return out;
  }

  _num(i, key, label, unit, min, max, step) {
    const v = this._slots[i][key];
    return `<label class="fld">${label}<span><input type="number" data-i="${i}" data-k="${key}" min="${min}" max="${max}" step="${step}" value="${esc(v)}">${unit}</span></label>`;
  }

  _row(s, i, n) {
    const m = MODES[s.mode] || MODES[4];
    const a = toQ(s.debut);
    const next = this._slots[i + 1];
    const finSel =
      i === n - 1
        ? `<span class="fixed">24:00</span>`
        : `<select data-fin="${i}">${this._timeOptions(a + 1, toQ(next.fin) - 1, toQ(s.fin))}</select>`;
    let fields = "";
    if (s.mode === 1) fields = this._num(i, "sh", "Jusqu'à", "%", 10, 100, 1) + this._num(i, "pc", "Puissance", "W", 100, 1000, 10);
    else if (s.mode === 2) fields = this._num(i, "sh", "Jusqu'à", "%", 10, 100, 1);
    else fields = this._num(i, "sl", "Jusqu'à", "%", 10, 100, 1) + this._num(i, "pd", "Max", "W", 100, 1000, 10);
    const modeOpts = Object.entries(MODES)
      .map(([k, v]) => `<option value="${k}" ${+k === s.mode ? "selected" : ""}>${v.name}</option>`)
      .join("");
    return `
      <div class="slot" style="border-left-color:${m.color}">
        <div class="slot-head">
          <ha-icon icon="${m.icon}" style="color:${m.color}"></ha-icon>
          <select class="mode" data-mode="${i}">${modeOpts}</select>
          <span class="hours"><span class="fixed">${s.debut}</span> → ${finSel}</span>
        </div>
        <div class="fields">${fields}
          <span class="tools">
            <button class="icon" data-split="${i}" title="Couper ce créneau en deux" ${n >= 12 || toQ(s.fin) - a < 2 ? "disabled" : ""}><ha-icon icon="mdi:content-cut"></ha-icon></button>
            <button class="icon" data-del="${i}" title="Supprimer ce créneau (fusion avec le précédent)" ${n <= 1 ? "disabled" : ""}><ha-icon icon="mdi:delete-outline"></ha-icon></button>
          </span>
        </div>
      </div>`;
  }

  _render() {
    const a = this._attrs;
    if (!a || !a.plans) {
      this.shadowRoot.innerHTML = `<ha-card><div class="pad">Entité ${esc(this._config && this._config.entity)} introuvable ou sans plannings.</div></ha-card>`;
      return;
    }
    if (!this._slots) this._syncFromState();
    const names = Object.keys(a.plans);
    const slots = this._slots || [];
    const isActive = a.planning === this._editName;
    const issue = a.planning_non_respecte;
    const modeOk = a.mode_pilotage === "Planning";
    const cur = a.creneau_actuel;
    const status = !modeOk
      ? `<span class="chip grey"><ha-icon icon="mdi:pause-circle-outline"></ha-icon>Mode ${esc(a.mode_pilotage)}</span>`
      : issue
        ? `<span class="chip red"><ha-icon icon="mdi:calendar-alert"></ha-icon>Écart détecté</span>`
        : `<span class="chip green"><ha-icon icon="mdi:calendar-check"></ha-icon>Planning respecté</span>`;
    const err = this._validate();
    const planOpts = names
      .map((n) => `<option value="${esc(n)}" ${n === this._editName ? "selected" : ""}>${esc(n)}${n === a.planning ? " ★ actif" : ""}</option>`)
      .join("");

    this.shadowRoot.innerHTML = `
      <style>${STYLE}</style>
      <ha-card>
        <div class="header">
          <div class="title"><ha-icon icon="mdi:calendar-clock"></ha-icon>${esc(this._config.title || "Plannings batterie")}</div>
          ${status}
        </div>
        <div class="sub">
          Actif : <b>${esc(a.planning)}</b>${cur ? ` · en cours : ${cur.debut}-${cur.fin} ${esc((MODES[cur.mode] || {}).name || "")}` : ""}
        </div>

        <div class="planbar">
          <select class="plan" ${this._busy ? "disabled" : ""}>${planOpts}</select>
          ${isActive ? `<span class="chip blue">Actif</span>` : `<button class="btn" data-act="activate" ${this._busy ? "disabled" : ""}>Activer</button>`}
          <span class="spacer"></span>
          <button class="icon" data-act="new" title="Nouveau planning"><ha-icon icon="mdi:plus"></ha-icon></button>
          <button class="icon" data-act="rename" title="Renommer"><ha-icon icon="mdi:pencil"></ha-icon></button>
          <button class="icon" data-act="delete" title="Supprimer le planning" ${names.length <= 1 ? "disabled" : ""}><ha-icon icon="mdi:delete"></ha-icon></button>
        </div>

        ${this._timeline(slots)}
        <div class="legend">${Object.values(MODES).map((m) => `<span><i style="background:${m.color}"></i>${m.short}</span>`).join("")}</div>

        <div class="slots">${slots.map((s, i) => this._row(s, i, slots.length)).join("")}</div>
        <div class="hint">✂ coupe un créneau en deux · 🗑 le fusionne avec le précédent · ${slots.length}/12 créneaux</div>

        ${this._message ? `<div class="msg ${this._message.error ? "err" : ""}">${esc(this._message.text)}</div>` : ""}
        ${this._dirty && err ? `<div class="msg err">${esc(err)}</div>` : ""}

        <div class="actions">
          ${this._dirty ? `<button class="btn flat" data-act="cancel">Annuler</button>` : ""}
          <button class="btn flat" data-act="saveas" ${this._busy ? "disabled" : ""}>Enregistrer sous…</button>
          <button class="btn primary" data-act="save" ${!this._dirty || this._busy || err ? "disabled" : ""}>
            ${this._dirty ? "Enregistrer" : "Enregistré"}</button>
        </div>
        <div class="actions second">
          <button class="btn flat" data-act="read" ${this._busy ? "disabled" : ""}><ha-icon icon="mdi:calendar-search"></ha-icon>Lire la batterie</button>
          <button class="btn flat" data-act="send" ${this._busy || !isActive ? "disabled" : ""} title="Renvoie le planning actif à la batterie">
            <ha-icon icon="mdi:calendar-arrow-right"></ha-icon>Renvoyer le planning actif</button>
        </div>
      </ha-card>`;
    this._bind();
  }

  _bind() {
    const r = this.shadowRoot;
    const sel = r.querySelector("select.plan");
    if (sel) sel.addEventListener("change", (e) => this._switchPlan(e.target.value));
    r.querySelectorAll("select[data-mode]").forEach((el) =>
      el.addEventListener("change", (e) => {
        this._slots[+e.target.dataset.mode].mode = +e.target.value;
        this._setDirty();
      })
    );
    r.querySelectorAll("select[data-fin]").forEach((el) =>
      el.addEventListener("change", (e) => this._changeFin(+e.target.dataset.fin, +e.target.value))
    );
    r.querySelectorAll("input[data-k]").forEach((el) =>
      el.addEventListener("change", (e) => {
        this._slots[+e.target.dataset.i][e.target.dataset.k] = Number(e.target.value);
        this._setDirty();
      })
    );
    r.querySelectorAll("button[data-split]").forEach((el) => el.addEventListener("click", () => this._split(+el.dataset.split)));
    r.querySelectorAll("button[data-del]").forEach((el) => el.addEventListener("click", () => this._remove(+el.dataset.del)));
    const acts = {
      activate: () => this._activate(),
      new: () => this._new(),
      rename: () => this._rename(),
      delete: () => this._delete(),
      cancel: () => this._cancel(),
      save: () => this._save(),
      saveas: () => this._saveAs(),
      read: () => this._call("read_plan", { day: "Mon", notify: true }, "Planning de la batterie : voir les notifications"),
      send: () => this._call("send_plan", {}, "Planning renvoyé à la batterie"),
    };
    r.querySelectorAll("button[data-act]").forEach((el) => el.addEventListener("click", () => acts[el.dataset.act]()));
  }
}

const STYLE = `
  ha-card { padding: 16px; }
  .pad { padding: 8px; }
  .header { display:flex; align-items:center; justify-content:space-between; gap:8px; }
  .title { font-size: 1.2em; font-weight: 500; display:flex; align-items:center; gap:8px; }
  .sub { color: var(--secondary-text-color); font-size: .9em; margin: 4px 0 12px; }
  .chip { display:inline-flex; align-items:center; gap:4px; padding:2px 10px; border-radius:12px; font-size:.85em; white-space:nowrap; }
  .chip ha-icon { --mdc-icon-size: 16px; }
  .chip.green { background: rgba(67,160,71,.15); color: #43a047; }
  .chip.red { background: rgba(219,68,55,.15); color: var(--error-color, #db4437); }
  .chip.grey { background: rgba(127,127,127,.15); color: var(--secondary-text-color); }
  .chip.blue { background: rgba(30,136,229,.15); color: #1e88e5; }
  .planbar { display:flex; align-items:center; gap:8px; margin-bottom: 12px; flex-wrap: wrap; }
  .spacer { flex: 1; }
  select, input { font: inherit; color: var(--primary-text-color); background: var(--card-background-color, #fff);
    border: 1px solid var(--divider-color, #ccc); border-radius: 6px; padding: 4px 6px; }
  select.plan { min-width: 160px; font-weight: 500; }
  input[type=number] { width: 58px; text-align: right; margin-right: 2px; }
  .timeline { position: relative; height: 22px; border-radius: 6px; overflow: hidden; background: var(--divider-color); }
  .seg { position:absolute; top:0; bottom:0; border-right: 1px solid var(--card-background-color, #fff); box-sizing: border-box; }
  .now { position:absolute; top:-2px; bottom:-2px; width:2px; background: var(--primary-text-color); }
  .ticks { position: relative; height: 16px; font-size: .7em; color: var(--secondary-text-color); margin-bottom: 4px; }
  .ticks span { position:absolute; transform: translateX(-50%); }
  .ticks span:first-child { transform: none; } .ticks span:last-child { transform: translateX(-100%); }
  .legend { display:flex; gap:12px; font-size:.8em; color: var(--secondary-text-color); margin: 4px 0 12px; flex-wrap: wrap; }
  .legend i { display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:4px; vertical-align: middle; }
  .slots { display:flex; flex-direction:column; gap:8px; }
  .slot { border: 1px solid var(--divider-color, #ddd); border-left-width: 4px; border-radius: 8px; padding: 8px 10px; }
  .slot-head { display:flex; align-items:center; gap:8px; }
  .slot-head select.mode { font-weight: 500; flex: 1; min-width: 0; }
  .hours { display:flex; align-items:center; gap:4px; }
  .fixed { font-variant-numeric: tabular-nums; padding: 4px 2px; }
  .tools { margin-left: auto; display:flex; }
  .fields { display:flex; align-items:center; gap:14px; flex-wrap: wrap; margin-top: 6px; padding-left: 32px; }
  .fld { display:flex; align-items:center; gap:6px; font-size:.9em; color: var(--secondary-text-color); }
  .hint { font-size: .75em; color: var(--secondary-text-color); margin: 6px 0; }
  .msg { padding: 8px 10px; border-radius: 6px; background: rgba(30,136,229,.12); margin: 8px 0; font-size: .9em; }
  .msg.err { background: rgba(219,68,55,.12); color: var(--error-color, #db4437); }
  .actions { display:flex; justify-content:flex-end; gap:8px; margin-top: 12px; flex-wrap: wrap; }
  .actions.second { justify-content: space-between; margin-top: 8px; border-top: 1px solid var(--divider-color); padding-top: 8px; }
  button { font: inherit; cursor: pointer; }
  button:disabled { opacity: .45; cursor: default; }
  .btn { display:inline-flex; align-items:center; gap:6px; border-radius: 18px; padding: 6px 14px; border: 1px solid var(--primary-color); background: none; color: var(--primary-color); }
  .btn.primary { background: var(--primary-color); color: var(--text-primary-color, #fff); }
  .btn.flat { border-color: transparent; }
  .btn ha-icon { --mdc-icon-size: 18px; }
  .icon { background:none; border:none; color: var(--secondary-text-color); padding: 4px; border-radius: 50%; display:inline-flex; }
  .icon:hover:not(:disabled) { background: rgba(127,127,127,.15); }
`;

if (!customElements.get("hoymiles-tou-card")) {
  customElements.define("hoymiles-tou-card", HoymilesTouCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "hoymiles-tou-card",
    name: "Hoymiles - plannings TOU",
    description: "Édition des plannings horaires de la batterie HiBattery",
    preview: false,
  });
  console.info(`%c HOYMILES-TOU-CARD %c ${CARD_VERSION} `, "background:#1e88e5;color:#fff", "");
}

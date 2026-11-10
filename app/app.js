/* Solarmaa Energie – App-Logik ohne Fremdbibliotheken.
   Holt Daten vom Pi (/api/...) oder simuliert sie im Demo-Modus (?demo). */
(() => {
  "use strict";

  const DEMO = window.SOLARMAA_DEMO === true || new URLSearchParams(location.search).has("demo");
  const $ = (id) => document.getElementById(id);
  const LOCALE = "de-CH";
  const nf0 = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 0 });
  const nf1 = new Intl.NumberFormat(LOCALE, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const nf2 = new Intl.NumberFormat(LOCALE, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  const fmtW = (w) => {
    if (w == null || isNaN(w)) return "–";
    const a = Math.abs(w);
    return a < 1000 ? `${nf0.format(a)} W` : `${nf1.format(a / 1000)} kW`;
  };
  const kwh = (wh) => (wh == null ? "–" : (Math.abs(wh) < 100000 ? nf1 : nf0).format(wh / 1000));
  const pct = (p) => (p == null ? "–" : `${nf0.format(p)} %`);
  const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const fromIso = (s) => { const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d); };
  const addDays = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
  const startOfDay = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate());

  /* ------------------------------------------------------------------ */
  /* Datenzugriff                                                        */
  /* ------------------------------------------------------------------ */
  const realApi = {
    async get(path) {
      const r = await fetch(path, { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    },
    info() { return this.get("/api/info"); },
    now() { return this.get("/api/now"); },
    history(p) { return this.get("/api/history?" + new URLSearchParams(p)); },
    subscribe(onData) {
      if (!("EventSource" in window)) return;
      const es = new EventSource("/api/stream");
      es.addEventListener("now", (e) => onData(JSON.parse(e.data)));
    },
  };

  /* Demo: gleiche Logik wie die Pi-Simulation, plus Jahreszeiten. */
  const demoApi = (() => {
    const KWP = 9.6, CAP = 10000;
    const season = (d) => 0.5 - 0.5 * Math.cos(((d.getMonth() + d.getDate() / 31 - 0.5) / 12) * 2 * Math.PI);
    const seed = (n) => { const x = Math.sin(n * 12.9898) * 43758.5453; return x - Math.floor(x); };
    function pvAt(ts) {
      const d = new Date(ts), s = season(d);
      const h = d.getHours() + d.getMinutes() / 60 + d.getSeconds() / 3600;
      const len = 8.5 + 7 * s, noon = 13.1;
      const x = Math.sin(Math.PI * (h - (noon - len / 2)) / len);
      if (x <= 0) return 0;
      const dayCloud = 0.45 + 0.55 * seed(Math.floor(ts / 86400000));
      const clouds = Math.min(1, dayCloud + 0.12 * Math.sin(ts / 9e5) + 0.06 * Math.sin(ts / 1.37e5));
      return KWP * 1000 * (0.28 + 0.52 * s) * Math.pow(x, 1.4) * clouds;
    }
    function loadAt(ts) {
      const d = new Date(ts), h = d.getHours() + d.getMinutes() / 60;
      const bump = (c, w, a) => a * Math.exp(-(((h - c) / w) ** 2));
      return 320 + 60 * Math.sin(ts / 3e5) + bump(7.2, 0.5, 1400) + bump(12.2, 0.6, 1900) + bump(18.8, 1, 2100) + bump(21.5, 0.8, 700);
    }
    function step(st, ts, dt) {
      const pv = pvAt(ts), load = loadAt(ts), surplus = pv - load;
      let bat;
      if (surplus > 0) bat = -Math.min(surplus, 3500, Math.max(0, ((CAP * 0.95 - st.soc) * 3600) / dt));
      else bat = Math.min(-surplus, 3500, Math.max(0, ((st.soc - CAP * 0.08) * 3600) / dt));
      st.soc -= (bat * dt) / 3600;
      const grid = load - pv - bat, h = dt / 3600;
      st.e.pv_wh += pv * h; st.e.load_wh += load * h;
      if (grid > 0) st.e.import_wh += grid * h; else st.e.export_wh -= grid * h;
      if (bat > 0) st.e.discharge_wh += bat * h; else st.e.charge_wh -= bat * h;
      return { ts: ts / 1000, pv_w: pv, load_w: load, grid_w: grid, battery_w: bat, soc: (100 * st.soc) / CAP };
    }
    const ratios = (e) => ({
      eigenverbrauch_pct: e.pv_wh > 50 ? Math.max(0, Math.min(100, (100 * (e.pv_wh - e.export_wh)) / e.pv_wh)) : null,
      autarkie_pct: e.load_wh > 50 ? Math.max(0, Math.min(100, (100 * (e.load_wh - e.import_wh)) / e.load_wh)) : null,
    });
    const blank = () => ({ pv_wh: 0, load_wh: 0, import_wh: 0, export_wh: 0, charge_wh: 0, discharge_wh: 0 });
    const cache = new Map();
    function simDay(day, until) {
      const t0 = startOfDay(day).getTime(), end = Math.min(t0 + 86400000, until ?? Infinity);
      const st = { soc: CAP * (0.15 + 0.2 * seed(t0 / 86400000 + 3)), e: blank() };
      const rows = []; let bucket = null;
      for (let t = t0; t < end; t += 60000) {
        const s = step(st, t, 60);
        const b = Math.floor(t / 300000) * 300;
        if (!bucket || bucket[0] !== b) { bucket = [b, 0, 0, 0, 0, 0, 0]; rows.push(bucket); }
        bucket[1] += s.pv_w; bucket[2] += s.load_w; bucket[3] += s.grid_w; bucket[4] += s.battery_w; bucket[5] += s.soc; bucket[6]++;
      }
      return { st, rows: rows.map((r) => [r[0], ...r.slice(1, 6).map((v) => v / r[6])]) };
    }
    function dayTotals(d) {
      const key = iso(d);
      if (!cache.has(key)) cache.set(key, simDay(d).st.e);
      return cache.get(key);
    }
    let live = null, today = null, lastTs = 0;
    function tick() {
      const now = Date.now();
      if (!today || iso(new Date(now)) !== today.key) {
        const sim = simDay(new Date(now), now);
        today = { key: iso(new Date(now)), st: sim.st };
        lastTs = now;
      }
      const dt = Math.max(1, (now - lastTs) / 1000);
      lastTs = now;
      live = step(today.st, now, dt);
      return live;
    }
    const nowPayload = () => {
      const l = tick();
      const e = today.st.e;
      return { live: l, heute: { day: today.key, ...e, ...ratios(e) }, status: "ok", fehler: null, demo: true };
    };
    return {
      async info() {
        let pv = 0, imp = 0, exp = 0, load = 0;
        for (let i = 1; i <= 200; i++) { const e = dayTotals(addDays(new Date(), -i)); pv += e.pv_wh; imp += e.import_wh; exp += e.export_wh; load += e.load_wh; }
        return {
          anlage: { name: "Familie Muster, Biel (Beispiel)", kwp: KWP, inbetriebnahme: iso(addDays(new Date(), -200)) },
          installateur: { name: "Solarmaa GmbH", telefon: "", email: "", web: "www.solarmaa.ch" },
          auswertung: {}, quelle: "demo", demo: true,
          gesamt: { seit: iso(addDays(new Date(), -200)), pv_wh: pv, import_wh: imp, export_wh: exp, load_wh: load },
        };
      },
      async now() { return nowPayload(); },
      async history(p) {
        const todayD = startOfDay(new Date());
        if (p.range === "day") {
          const d = fromIso(p.date);
          const until = iso(d) === iso(todayD) ? Date.now() : undefined;
          return { range: "day", date: p.date, rows: d > todayD ? [] : simDay(d, until).rows };
        }
        if (p.range === "week" || p.range === "month") {
          const end = fromIso(p.end), n = p.range === "week" ? 7 : 30, rows = [];
          for (let i = n - 1; i >= 0; i--) {
            const d = addDays(end, -i);
            if (d > todayD) continue;
            const e = iso(d) === iso(todayD) ? today?.st.e ?? blank() : dayTotals(d);
            rows.push({ day: iso(d), ...e, ...ratios(e) });
          }
          return { range: p.range, rows };
        }
        const y = Number(p.year), rows = [];
        for (let m = 0; m < 12; m++) {
          const e = blank(); let any = false;
          for (let d = new Date(y, m, 1); d.getMonth() === m && d <= todayD; d = addDays(d, 1)) {
            const t = dayTotals(d); any = true;
            for (const k in e) e[k] += t[k];
          }
          if (any) rows.push({ month: `${y}-${String(m + 1).padStart(2, "0")}`, ...e, ...ratios(e) });
        }
        return { range: "year", year: y, rows };
      },
      subscribe(onData) { setInterval(() => onData(nowPayload()), 3000); },
    };
  })();

  const api = DEMO ? demoApi : realApi;

  /* ------------------------------------------------------------------ */
  /* Anzeige: Kopf, Fluss, Heute                                         */
  /* ------------------------------------------------------------------ */
  let info = null;
  let lastMsg = 0;

  function renderInfo(i) {
    info = i;
    $("plantName").textContent = i.anlage.name;
    document.title = `${i.anlage.name} · Solarmaa`;
    $("demoBand").hidden = !i.demo;
    const facts = [`${nf1.format(i.anlage.kwp)} kWp`];
    if (i.anlage.inbetriebnahme) facts.push(`in Betrieb seit ${fromIso(i.anlage.inbetriebnahme).toLocaleDateString(LOCALE, { month: "long", year: "numeric" })}`);
    $("plantFacts").textContent = facts.join(" · ");
    const g = i.gesamt;
    if (g && g.seit && g.pv_wh > 0) {
      $("totals").textContent = `Seit ${fromIso(g.seit).toLocaleDateString(LOCALE)} produziert: ${nf0.format(g.pv_wh / 1000)} kWh`;
    }
    if ((i.haus && i.haus.aktiv) || i.demo) {
      $("hausLinkBox").hidden = false;
      if (i.demo && !(i.haus && i.haus.aktiv)) $("hausLink").href = "haus.html?demo";
    }
    const inst = i.installateur;
    $("instName").textContent = inst.name;
    $("instContact").textContent = [inst.telefon, inst.email, inst.web].filter(Boolean).join(" · ");
  }

  function setLine(id, w, reverse) {
    const el = $(id);
    const on = w != null && Math.abs(w) > 15;
    el.classList.toggle("on", on);
    el.classList.toggle("rev", on && reverse);
    if (on) el.style.animationDuration = `${Math.max(0.5, 2.6 - Math.log10(Math.abs(w)) * 0.55).toFixed(2)}s`;
  }

  function setRing(id, p) {
    const root = $(id);
    root.querySelector(".pct").textContent = pct(p);
    root.querySelector(".r-fg").style.strokeDashoffset = String(314.16 * (1 - (p ?? 0) / 100));
  }

  function renderNow(p) {
    lastMsg = Date.now();
    const st = $("status");
    if (p.status === "ok") {
      st.dataset.state = "ok";
      $("statusText").textContent = p.demo ? "Demo live" : "Live";
    } else {
      st.dataset.state = "err";
      $("statusText").textContent = "Anlage antwortet nicht";
      st.title = p.fehler || "";
    }
    const l = p.live;
    if (l) {
      $("vPv").textContent = fmtW(l.pv_w);
      $("vHome").textContent = fmtW(l.load_w);
      $("vGrid").textContent = fmtW(l.grid_w);
      $("lGrid").textContent = l.grid_w == null ? "Netz" : l.grid_w > 15 ? "Netzbezug" : l.grid_w < -15 ? "Einspeisung" : "Netz";
      setLine("lnPv", l.pv_w, false);
      setLine("lnHome", l.load_w, false);
      setLine("lnGrid", l.grid_w, l.grid_w != null && l.grid_w < 0);
      const hasBat = l.battery_w != null || l.soc != null;
      $("nodeBat").classList.toggle("off", !hasBat);
      $("lnBat").style.display = hasBat ? "" : "none";
      if (hasBat) {
        $("vBat").textContent = l.soc != null ? `${nf0.format(l.soc)} %` : "–";
        $("lBat").textContent = l.battery_w == null ? "Batterie" : l.battery_w > 15 ? `entlädt ${fmtW(l.battery_w)}` : l.battery_w < -15 ? `lädt ${fmtW(l.battery_w)}` : "Batterie";
        setLine("lnBat", l.battery_w, l.battery_w != null && l.battery_w < 0);
        const fillH = Math.max(0, Math.min(1, (l.soc ?? 0) / 100)) * 19;
        const f = $("batFill"); f.setAttribute("height", fillH.toFixed(1)); f.setAttribute("y", (9.5 - fillH).toFixed(1));
      } else {
        $("vBat").textContent = ""; $("lBat").textContent = "";
      }
      $("clock").textContent = `Stand ${new Date(l.ts * 1000).toLocaleTimeString(LOCALE)}`;
    }
    const h = p.heute;
    if (h) {
      $("tPv").textContent = kwh(h.pv_wh);
      $("tHome").textContent = kwh(h.load_wh);
      $("tExp").textContent = kwh(h.export_wh);
      $("tImp").textContent = kwh(h.import_wh);
      setRing("ringSelf", h.eigenverbrauch_pct);
      setRing("ringAut", h.autarkie_pct);
      $("todayDate").textContent = fromIso(h.day).toLocaleDateString(LOCALE, { weekday: "long", day: "numeric", month: "long" });
      renderExtra(h);
    }
  }

  function renderExtra(h) {
    const a = info?.auswertung || {};
    const parts = [];
    if (a.tarif_bezug_rp != null) {
      const selfKwh = Math.max(0, (h.pv_wh - h.export_wh) / 1000);
      let chf = (selfKwh * a.tarif_bezug_rp) / 100;
      if (a.verguetung_rp != null) chf += ((h.export_wh / 1000) * a.verguetung_rp) / 100;
      parts.push(`Heute rund CHF ${nf2.format(chf)} Stromkosten gespart`);
    }
    if (a.co2_kg_pro_kwh != null) parts.push(`${nf1.format((h.pv_wh / 1000) * a.co2_kg_pro_kwh)} kg CO₂ vermieden`);
    $("extra").hidden = parts.length === 0;
    $("extra").textContent = parts.join(" · ");
  }

  /* ------------------------------------------------------------------ */
  /* Verlauf                                                             */
  /* ------------------------------------------------------------------ */
  const hist = { range: "day", anchor: startOfDay(new Date()), data: null };
  const SVGNS = "http://www.w3.org/2000/svg";
  const el = (tag, attrs, text) => {
    const n = document.createElementNS(SVGNS, tag);
    for (const k in attrs) n.setAttribute(k, attrs[k]);
    if (text != null) n.textContent = text;
    return n;
  };
  // Obergrenze so wählen, dass 4 Rasterlinien runde Werte ergeben (z. B. 0 · 0.5 · 1 · 1.5 · 2)
  function niceMax(v) {
    if (v <= 0) return 1;
    const raw = v / 4, p = Math.pow(10, Math.floor(Math.log10(raw)));
    for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= raw) return 4 * m * p;
    return 40 * p;
  }

  function histParams() {
    const a = hist.anchor;
    if (hist.range === "day") return { range: "day", date: iso(a) };
    if (hist.range === "year") return { range: "year", year: a.getFullYear() };
    return { range: hist.range, end: iso(a) };
  }

  function histLabel() {
    const a = hist.anchor, today = startOfDay(new Date());
    if (hist.range === "day") {
      if (iso(a) === iso(today)) return "Heute";
      if (iso(a) === iso(addDays(today, -1))) return "Gestern";
      return a.toLocaleDateString(LOCALE, { weekday: "short", day: "numeric", month: "long", year: "numeric" });
    }
    if (hist.range === "year") return String(a.getFullYear());
    const start = addDays(a, hist.range === "week" ? -6 : -29);
    const o = { day: "numeric", month: "short" };
    return `${start.toLocaleDateString(LOCALE, o)} – ${a.toLocaleDateString(LOCALE, { ...o, year: "numeric" })}`;
  }

  function shift(dir) {
    const a = hist.anchor;
    if (hist.range === "day") hist.anchor = addDays(a, dir);
    else if (hist.range === "week") hist.anchor = addDays(a, 7 * dir);
    else if (hist.range === "month") hist.anchor = addDays(a, 30 * dir);
    else hist.anchor = new Date(a.getFullYear() + dir, 11, 31);
    const today = startOfDay(new Date());
    if (hist.anchor > today) hist.anchor = today;
    loadHistory();
  }

  async function loadHistory() {
    $("histLabel").textContent = histLabel();
    const today = startOfDay(new Date());
    $("next").disabled = hist.range === "year" ? hist.anchor.getFullYear() >= today.getFullYear() : iso(hist.anchor) >= iso(today);
    try {
      hist.data = await api.history(histParams());
    } catch (e) {
      hist.data = null;
    }
    drawChart();
  }

  function frame() {
    const svg = $("chart");
    const W = Math.max(280, $("chartBox").clientWidth), H = 260;
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.replaceChildren();
    return { svg, W, H, L: 44, R: 10, T: 24, B: 26 };
  }

  function yAxis(f, max, unit) {
    const { svg, W, H, L, R, T, B } = f;
    for (let i = 0; i <= 4; i++) {
      const v = (max * i) / 4, y = H - B - ((H - B - T) * i) / 4;
      svg.append(el("line", { x1: L, x2: W - R, y1: y, y2: y, class: "grid" }));
      svg.append(el("text", { x: L - 6, y: y + 4, "text-anchor": "end" }, (max < 4 && v % 1 ? (v * 10 % 1 ? nf2 : nf1) : nf0).format(v)));
    }
    svg.append(el("text", { x: 4, y: 11, "text-anchor": "start" }, unit));
  }

  function empty(f, msg) {
    f.svg.append(el("text", { x: f.W / 2, y: f.H / 2, "text-anchor": "middle", class: "empty" }, msg));
    $("histSum").textContent = "";
  }

  function drawChart() {
    $("tip").hidden = true;
    const f = frame();
    const d = hist.data;
    if (!d) return empty(f, "Verlauf konnte nicht geladen werden");
    if (!d.rows.length) return empty(f, "Für diesen Zeitraum gibt es noch keine Daten");
    if (d.range === "day") drawDay(f, d); else drawBars(f, d);
  }

  function drawDay(f, d) {
    const { svg, W, H, L, R, T, B } = f;
    const t0 = fromIso(d.date).getTime() / 1000, span = 86400;
    const rows = d.rows;
    const max = niceMax(Math.max(...rows.map((r) => Math.max(r[1] ?? 0, r[2] ?? 0))) / 1000);
    yAxis(f, max, "kW");
    const x = (ts) => L + ((ts - t0) / span) * (W - L - R);
    const y = (w) => H - B - ((w / 1000) / max) * (H - B - T);
    for (let h = 0; h <= 24; h += 6) {
      svg.append(el("text", { x: x(t0 + h * 3600), y: H - 8, "text-anchor": h === 0 ? "start" : h === 24 ? "end" : "middle" }, `${String(h).padStart(2, "0")}:00`));
    }
    const path = (idx) => {
      let s = "", prev = null;
      for (const r of rows) {
        if (r[idx] == null) { prev = null; continue; }
        const gap = prev && r[0] - prev[0] > 900;
        s += `${!prev || gap ? "M" : "L"}${x(r[0]).toFixed(1)} ${y(r[idx]).toFixed(1)}`;
        prev = r;
      }
      return s;
    };
    const pv = path(1);
    if (pv) {
      const first = rows.find((r) => r[1] != null), last = [...rows].reverse().find((r) => r[1] != null);
      svg.append(el("path", { d: `${pv}L${x(last[0]).toFixed(1)} ${y(0)}L${x(first[0]).toFixed(1)} ${y(0)}Z`, class: "pv-area" }));
      svg.append(el("path", { d: pv, class: "pv-line" }));
    }
    const home = path(2);
    if (home) svg.append(el("path", { d: home, class: "home-line" }));

    // Summen
    let pvWh = 0, homeWh = 0;
    for (let i = 1; i < rows.length; i++) {
      const dt = Math.min(rows[i][0] - rows[i - 1][0], 900) / 3600;
      pvWh += (rows[i][1] ?? 0) * dt; homeWh += (rows[i][2] ?? 0) * dt;
    }
    const peak = rows.reduce((a, r) => ((r[1] ?? 0) > (a[1] ?? 0) ? r : a), rows[0]);
    $("histSum").textContent = `Produktion ${kwh(pvWh)} kWh · Verbrauch ${kwh(homeWh)} kWh · Spitze ${fmtW(peak[1])} um ${new Date(peak[0] * 1000).toLocaleTimeString(LOCALE, { hour: "2-digit", minute: "2-digit" })}`;

    // Fadenkreuz
    const cursor = el("line", { y1: T, y2: H - B, class: "cursor", visibility: "hidden" });
    svg.append(cursor);
    const move = (ev) => {
      const box = svg.getBoundingClientRect();
      const px = ((ev.clientX - box.left) / box.width) * W;
      const ts = t0 + ((px - L) / (W - L - R)) * span;
      let best = rows[0];
      for (const r of rows) if (Math.abs(r[0] - ts) < Math.abs(best[0] - ts)) best = r;
      const cx = x(best[0]);
      cursor.setAttribute("x1", cx); cursor.setAttribute("x2", cx); cursor.setAttribute("visibility", "visible");
      const tip = $("tip");
      tip.hidden = false;
      tip.style.left = `${Math.min(Math.max((cx / W) * box.width, 70), box.width - 70)}px`;
      tip.textContent = `${new Date(best[0] * 1000).toLocaleTimeString(LOCALE, { hour: "2-digit", minute: "2-digit" })}  ·  ☀ ${fmtW(best[1])}  ·  ⌂ ${fmtW(best[2])}`;
    };
    svg.onpointermove = move;
    svg.onpointerdown = move;
    svg.onpointerleave = () => { cursor.setAttribute("visibility", "hidden"); $("tip").hidden = true; };
  }

  function drawBars(f, d) {
    const { svg, W, H, L, R, T, B } = f;
    let slots;
    if (d.range === "year") {
      const byKey = Object.fromEntries(d.rows.map((r) => [r.month, r]));
      slots = Array.from({ length: 12 }, (_, m) => {
        const key = `${d.year}-${String(m + 1).padStart(2, "0")}`;
        return { label: new Date(d.year, m, 1).toLocaleDateString(LOCALE, { month: "narrow" }), title: new Date(d.year, m, 1).toLocaleDateString(LOCALE, { month: "long" }), r: byKey[key] };
      });
    } else {
      const n = d.range === "week" ? 7 : 30, end = hist.anchor;
      const byKey = Object.fromEntries(d.rows.map((r) => [r.day, r]));
      slots = Array.from({ length: n }, (_, i) => {
        const day = addDays(end, i - n + 1);
        const label = d.range === "week" ? day.toLocaleDateString(LOCALE, { weekday: "short" }) : (day.getDate() % 5 === 0 || n - i === n ? String(day.getDate()) : "");
        return { label, title: day.toLocaleDateString(LOCALE, { weekday: "short", day: "numeric", month: "short" }), r: byKey[iso(day)] };
      });
    }
    const max = niceMax(Math.max(...slots.map((s) => (s.r ? Math.max(s.r.pv_wh, s.r.load_wh) : 0))) / 1000);
    yAxis(f, max, "kWh");
    const slotW = (W - L - R) / slots.length;
    const barW = Math.max(2, Math.min(18, slotW * 0.36));
    const y = (wh) => H - B - ((wh / 1000) / max) * (H - B - T);
    let sumPv = 0, sumHome = 0;
    slots.forEach((s, i) => {
      const cx = L + slotW * (i + 0.5);
      if (s.label) svg.append(el("text", { x: cx, y: H - 8, "text-anchor": "middle" }, s.label));
      if (!s.r) return;
      sumPv += s.r.pv_wh; sumHome += s.r.load_wh;
      const g = el("g", {});
      g.append(el("rect", { x: cx - barW - 1, y: y(s.r.pv_wh), width: barW, height: Math.max(0, y(0) - y(s.r.pv_wh)), rx: 2, class: "bar-pv" }));
      g.append(el("rect", { x: cx + 1, y: y(s.r.load_wh), width: barW, height: Math.max(0, y(0) - y(s.r.load_wh)), rx: 2, class: "bar-home" }));
      g.append(el("title", {}, `${s.title}: Produktion ${kwh(s.r.pv_wh)} kWh, Verbrauch ${kwh(s.r.load_wh)} kWh`));
      svg.append(g);
    });
    svg.onpointermove = svg.onpointerdown = (ev) => {
      const box = svg.getBoundingClientRect();
      const px = ((ev.clientX - box.left) / box.width) * W;
      const i = Math.floor((px - L) / slotW);
      const s = slots[i];
      const tip = $("tip");
      if (!s || !s.r) { tip.hidden = true; return; }
      tip.hidden = false;
      tip.style.left = `${Math.min(Math.max(((L + slotW * (i + 0.5)) / W) * box.width, 90), box.width - 90)}px`;
      tip.textContent = `${s.title}  ·  ☀ ${kwh(s.r.pv_wh)}  ·  ⌂ ${kwh(s.r.load_wh)} kWh`;
    };
    svg.onpointerleave = () => { $("tip").hidden = true; };
    $("histSum").textContent = `Produktion ${kwh(sumPv)} kWh · Verbrauch ${kwh(sumHome)} kWh`;
  }

  /* ------------------------------------------------------------------ */
  /* Start                                                               */
  /* ------------------------------------------------------------------ */
  document.querySelectorAll(".tabs button").forEach((b) => {
    b.addEventListener("click", () => {
      document.querySelectorAll(".tabs button").forEach((x) => x.setAttribute("aria-selected", String(x === b)));
      hist.range = b.dataset.range;
      hist.anchor = startOfDay(new Date());
      loadHistory();
    });
  });
  $("prev").addEventListener("click", () => shift(-1));
  $("next").addEventListener("click", () => shift(1));
  let rt;
  window.addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(drawChart, 150); });

  async function poll() {
    try { renderNow(await api.now()); }
    catch (e) {
      $("status").dataset.state = "err";
      $("statusText").textContent = "Keine Verbindung zum Gerät";
    }
  }

  async function start() {
    try { renderInfo(await api.info()); } catch (e) { /* Kopf bleibt mit Standardtext */ }
    await poll();
    loadHistory();
    api.subscribe(renderNow);
    // Fallback, falls der Live-Kanal hängt (z. B. Proxy, WLAN-Wechsel)
    setInterval(() => { if (Date.now() - lastMsg > 15000) poll(); }, 5000);
    // Tageskurve jede Minute nachführen
    setInterval(() => { if (hist.range === "day" && iso(hist.anchor) === iso(startOfDay(new Date()))) loadHistory(); }, 60000);
  }
  start();
})();

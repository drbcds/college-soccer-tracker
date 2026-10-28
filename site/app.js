/* College Soccer Tracker — single-page app reading static JSON from ./data */
(() => {
  "use strict";

  const $main = document.getElementById("main");
  const cache = new Map();
  let refreshTimer = null;
  let TEAMS = null;           // seo -> team
  let SEARCH = null;

  // ---------------------------------------------------------------- helpers
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pad = (n) => String(n).padStart(2, "0");

  async function load(path, { fresh = false } = {}) {
    if (!fresh && cache.has(path)) return cache.get(path);
    const url = `data/${path}` + (fresh ? `?t=${Math.floor(Date.now() / 30000)}` : "");
    const res = await fetch(url);
    if (!res.ok) { if (res.status === 404) return null; throw new Error(`Couldn't load ${path} (${res.status})`); }
    const data = await res.json();
    cache.set(path, data);
    return data;
  }

  async function teams() {
    if (TEAMS) return TEAMS;
    const list = (await load("teams.json")) || [];
    TEAMS = Object.fromEntries(list.map((t) => [t.seo, t]));
    return TEAMS;
  }
  const team = (seo) => (TEAMS && TEAMS[seo]) || { seo, name: seo, color: "#999" };
  const kit = (seo, lg = false) => `<span class="kit${lg ? " lg" : ""}" style="--c:${esc(team(seo).color || "#999")}" aria-hidden="true"></span>`;

  const CONF = {
    acc: "ACC", "big-east": "Big East", "big-ten": "Big Ten", "pac-12": "Pac-12", wcc: "WCC", "big-west": "Big West",
    "sun-belt": "Sun Belt", aac: "American", american: "American", "atlantic-10": "Atlantic 10", a10: "Atlantic 10",
    caa: "CAA", "coastal": "CAA", asun: "ASUN", "atlantic-sun": "ASUN", horizon: "Horizon League", summit: "Summit League",
    "summit-league": "Summit League", mvc: "Missouri Valley", mac: "MAC", wac: "WAC", ivy: "Ivy League", "ivy-league": "Ivy League",
    patriot: "Patriot League", nec: "NEC", maac: "MAAC", metro: "MAAC", "america-east": "America East", socon: "SoCon", southern: "SoCon",
    "big-south": "Big South", ovc: "OVC", "mountain-west": "Mountain West", "big-12": "Big 12", sec: "SEC", dii_independent: "Independent",
    "di-independent": "Independent", "dii-independent": "Independent", independent: "Independent",
    gliac: "GLIAC", gmac: "G-MAC", glvc: "GLVC", gac: "GAC", "lone-star": "Lone Star", psac: "PSAC", "cacc": "CACC", "ne10": "NE10",
    "sunshine-state": "Sunshine State", ssc: "Sunshine State", "peach-belt": "Peach Belt", "gulf-south": "Gulf South", gsc: "Gulf South",
    "south-atlantic": "South Atlantic", sac: "South Atlantic", "conference-carolinas": "Conference Carolinas", cc: "Conference Carolinas",
    mec: "Mountain East", "mountain-east": "Mountain East", "east-coast": "East Coast", ecc: "East Coast", ccaa: "CCAA",
    pacwest: "PacWest", rmac: "RMAC", gnac: "GNAC", nsic: "NSIC", miaa: "MIAA", "heartland": "Heartland", ciaa: "CIAA", siac: "SIAC",
  };
  const confName = (c) => !c ? "Other" : CONF[c] || (c.length <= 5 ? c.toUpperCase() : c.replace(/-/g, " ").replace(/\b\w/g, (m) => m.toUpperCase()));

  const DIVS = { "1": "NCAA Division I", "2": "NCAA Division II", J1: "NJCAA Division I", J2: "NJCAA Division II", J3: "NJCAA Division III", J: "NJCAA" };
  const DIV_ORDER = ["1", "2", "J1", "J2", "J3", "J"];
  const divLabel = (d) => DIVS[String(d)] || "";
  const divRank = (d) => { const i = DIV_ORDER.indexOf(String(d)); return i < 0 ? 99 : i; };
  // Division filter values: "all", "1", "2", "J" (any NJCAA division)
  const divMatch = (d, f) => f === "all" || (f === "J" ? String(d).startsWith("J") : String(d) === f);
  const DIV_TABS = [["1", "NCAA D-I"], ["2", "NCAA D-II"], ["J1", "NJCAA D-I"], ["J2", "NJCAA D-II"], ["J3", "NJCAA D-III"]];

  // US (Eastern) calendar date: games are filed under their US date
  const etDate = (d = new Date()) => {
    const p = new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit" }).format(d);
    return p; // YYYY-MM-DD
  };
  const addDays = (iso, n) => { const d = new Date(iso + "T12:00:00Z"); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };
  const dayLabel = (iso) => {
    const d = new Date(iso + "T12:00:00Z");
    return { wd: d.toLocaleDateString(undefined, { weekday: "short", timeZone: "UTC" }), dm: d.getUTCDate(), mon: d.toLocaleDateString(undefined, { month: "short", timeZone: "UTC" }) };
  };
  const longDate = (iso) => new Date(iso + "T12:00:00Z").toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric", year: "numeric", timeZone: "UTC" });
  const localTime = (epoch) => new Date(epoch * 1000).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  // Game dates are US dates (as listed by the schools), not the viewer's local date
  const shortDate = (g) => g.date ? new Date(g.date + "T12:00:00Z").toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" }) : "";

  function minute(clk) {
    if (!clk || !clk.includes(":")) return "";
    const [m, s] = clk.split(":").map(Number);
    return `${m + (m || s ? 1 : 0)}'`;
  }

  function statusText(g) {
    switch (g.s) {
      case "live": {
        const p = (g.per || "").toUpperCase();
        if (p.includes("HALF") && !p.match(/1ST|2ND/)) return "HT";
        return minute(g.clk) || g.per || "Live";
      }
      case "final": {
        const p = (g.per || "").toUpperCase();
        return p.includes("OT") ? "FT (OT)" : p.includes("SO") || p.includes("PK") ? "FT (PKs)" : "FT";
      }
      case "pre": return g.tm && g.t ? localTime(g.t) : "TBA";
      case "postponed": return "Postp.";
      case "canceled": case "cancelled": return "Canc.";
      default: return g.s || "";
    }
  }

  function setNav(name) {
    document.querySelectorAll("[data-nav]").forEach((a) => {
      if (a.dataset.nav === name) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    });
  }

  function autoRefresh(fn, ms = 60000) {
    clearInterval(refreshTimer);
    refreshTimer = fn ? setInterval(() => { if (!document.hidden) fn(); }, ms) : null;
  }

  // ---------------------------------------------------------------- match row
  function matchRow(g) {
    const hW = g.w && g.w === g.h, aW = g.w && g.w === g.a;
    const done = g.s === "final";
    const cls = (w, other) => (done && g.w ? (w ? "won" : other ? "lost" : "") : "");
    const live = g.s === "live";
    const side = (seo, rank, w, other) => `
      <div class="side ${cls(w, other)}">${kit(seo)}<span class="nm">${rank ? `<span class="rank">${rank}</span> ` : ""}${esc(team(seo).name)}</span></div>`;
    const sc = (v, w, other) => `<span class="${done && g.w && !w && other ? "lost" : ""}">${v ?? ""}</span>`;
    return `<a class="match" href="#/game/${g.id}">
      <div class="when${live ? " live" : ""}">${esc(statusText(g))}</div>
      <div class="sides">${side(g.h, g.hr, hW, aW)}${side(g.a, g.ar, aW, hW)}</div>
      <div class="scores${live ? " live" : ""}">${sc(g.hs, hW, aW)}${sc(g.as, aW, hW)}</div>
    </a>`;
  }

  // ---------------------------------------------------------------- scores view
  const scoresState = { div: "all", liveOnly: false };

  async function viewScores(params) {
    setNav("scores");
    await teams();
    const today = etDate();
    const date = params.get("date") || today;
    const dates = (await load("dates.json", { fresh: true })) || {};
    const strip = [];
    for (let i = -7; i <= 7; i++) strip.push(addDays(date, i));

    const draw = async (fresh) => {
      const games = (await load(`days/${date}.json`, { fresh })) || [];
      let list = games.filter((g) => divMatch(g.div, scoresState.div));
      if (scoresState.liveOnly) list = list.filter((g) => g.s === "live");
      const groups = new Map();
      for (const g of list) {
        const hc = team(g.h).conf, ac = team(g.a).conf;
        const key = g.rd ? g.rd : hc && hc === ac ? confName(hc) : "Non-conference";
        const div = String(g.div ?? "");
        const k = `${key}|${div}`;
        if (!groups.has(k)) groups.set(k, { name: key, div, games: [] });
        groups.get(k).games.push(g);
      }
      const sorted = [...groups.values()].sort((a, b) =>
        (b.games.some((g) => g.s === "live") - a.games.some((g) => g.s === "live")) ||
        divRank(a.div) - divRank(b.div) || (a.name === "Non-conference") - (b.name === "Non-conference") || a.name.localeCompare(b.name));
      for (const gr of sorted) gr.games.sort((a, b) => (a.t || 0) - (b.t || 0) || String(a.id).localeCompare(String(b.id)));
      const liveN = games.filter((g) => g.s === "live").length;

      document.getElementById("list").innerHTML = sorted.length ? sorted.map((gr) => `
        <section class="group" aria-label="${esc(gr.name)}">
          <h2>${esc(gr.name)} <span>${esc(divLabel(gr.div))}</span></h2>
          ${gr.games.map(matchRow).join("")}
        </section>`).join("") : `<div class="empty-state">${scoresState.liveOnly ? "No games are live right now." : `No ${scoresState.div === "all" ? "" : ({ "1": "NCAA Division I ", "2": "NCAA Division II ", J: "NJCAA " })[scoresState.div]}games on ${esc(longDate(date))}.`}</div>`;
      const lb = document.getElementById("livebtn");
      if (lb) lb.querySelector("span").textContent = liveN ? `Live now (${liveN})` : "Live now";
    };

    $main.innerHTML = `
      <h1 class="sr-only" style="position:absolute;left:-9999px">Scores for ${esc(longDate(date))}</h1>
      <div class="toolbar">
        <div class="days" role="group" aria-label="Choose a day">
          ${strip.map((d) => { const l = dayLabel(d); return `
            <button class="day${dates[d] ? "" : " empty"}" data-date="${d}" aria-pressed="${d === date}" title="${dates[d] || 0} games">
              <small>${d === today ? "Today" : esc(l.wd)}</small><b>${l.dm}</b><small>${esc(l.mon)}</small>
            </button>`; }).join("")}
        </div>
        <input class="datepick" type="date" value="${date}" aria-label="Pick a date">
      </div>
      <div class="toolbar">
        <div class="seg" role="group" aria-label="Division">
          ${[["all", "All"], ["1", "NCAA D-I"], ["2", "NCAA D-II"], ["J", "NJCAA"]].map(([v, l]) => `<button data-div="${v}" aria-pressed="${scoresState.div === v}">${l}</button>`).join("")}
        </div>
        <label class="toggle" id="livebtn"><input type="checkbox" ${scoresState.liveOnly ? "checked" : ""}> <span>Live now</span></label>
      </div>
      <div id="list"><p class="loading">Loading games…</p></div>`;

    $main.querySelectorAll(".day").forEach((b) => b.addEventListener("click", () => go(`#/?date=${b.dataset.date}`)));
    $main.querySelector(".datepick").addEventListener("change", (e) => e.target.value && go(`#/?date=${e.target.value}`));
    $main.querySelectorAll("[data-div]").forEach((b) => b.addEventListener("click", () => {
      scoresState.div = b.dataset.div;
      $main.querySelectorAll("[data-div]").forEach((x) => x.setAttribute("aria-pressed", x === b));
      draw(false);
    }));
    $main.querySelector("#livebtn input").addEventListener("change", (e) => { scoresState.liveOnly = e.target.checked; draw(false); });
    $main.querySelector(`.day[aria-pressed="true"]`)?.scrollIntoView({ inline: "center", block: "nearest" });

    await draw(true);
    const recent = date >= addDays(today, -1) && date <= today;
    autoRefresh(recent ? () => draw(true) : null);
  }

  // ---------------------------------------------------------------- game view
  function evIcon(type) {
    if (type === "goal") return `<svg class="icon" viewBox="0 0 20 20" aria-label="Goal"><circle cx="10" cy="10" r="8.5" fill="var(--surface)" stroke="currentColor" stroke-width="1.5"/><path d="M10 5.5l3.6 2.6-1.4 4.2H7.8L6.4 8.1z" fill="currentColor"/></svg>`;
    if (type === "yellow") return `<span class="card-y" aria-label="Yellow card"></span>`;
    if (type === "red") return `<span class="card-r" aria-label="Red card"></span>`;
    if (type === "sub") return `<svg class="icon" viewBox="0 0 20 20" aria-label="Substitution"><path d="M6 3v11M3 11l3 3 3-3" fill="none" stroke="#2F8F55" stroke-width="1.8"/><path d="M14 17V6M11 9l3-3 3 3" fill="none" stroke="#C8463E" stroke-width="1.8"/></svg>`;
    return "";
  }

  function teamStatRows(g) {
    const H = g.stats?.home, A = g.stats?.away;
    if (!H || !A) return "";
    const n = (v) => (v === null || v === undefined || v === "" ? null : Number(v));
    const rows = [
      ["Shots", n(H.shots), n(A.shots)],
      ["Shots on goal", n(H.shotsOnGoal), n(A.shotsOnGoal)],
      ["Corner kicks", n(H.corners), n(A.corners)],
      ["Saves", n(H.goalie?.saves), n(A.goalie?.saves)],
      ["Fouls", n(H.penalties?.fouls), n(A.penalties?.fouls)],
      ["Offsides", n(H.offsides), n(A.offsides)],
      ["Yellow cards", n(H.penalties?.yellowCards), n(A.penalties?.yellowCards)],
      ["Red cards", n(H.penalties?.redCards), n(A.penalties?.redCards)],
    ].filter(([, h, a]) => h !== null && a !== null && !Number.isNaN(h) && !Number.isNaN(a));
    return rows.map(([l, h, a]) => {
      return `<div class="statbar"><div class="lbl">${l}</div><div class="v">${h}</div>
        <div class="bar">${h + a ? `<i style="flex:${h || 0.0001}"></i><i style="flex:${a || 0.0001}"></i>` : ""}</div>
        <div class="v">${a}</div></div>`;
    }).join("");
  }

  function timelineHtml(g) {
    const ev = (g.events || []).filter((e) => e.type !== "sub" || e.minute !== null);
    if (!ev.length) return `<p class="muted">No events recorded yet.</p>`;
    let html = "", ht = false;
    for (const e of ev) {
      if (!ht && e.minute > 45 && ev.some((x) => x.minute <= 45)) { html += `<li class="divider">Half-time</li>`; ht = true; }
      const home = e.team === g.h;
      let main = "", sub = "";
      if (e.type === "goal") { main = `<b>${esc(e.player)}</b>`; sub = [e.assist && `Assist: ${esc(e.assist)}`, e.score && `${e.score[0]}–${e.score[1]}`].filter(Boolean).join(", "); }
      else if (e.type === "sub") { main = `${esc(e.player)}`; sub = `<span class="sub-arrow">off: ${esc(e.off)}</span>`; }
      else { main = esc(e.player || ""); }
      const cell = `<div class="ev">${home ? "" : evIcon(e.type)}<div>${main}${sub ? `<small>${sub}</small>` : ""}</div>${home ? evIcon(e.type) : ""}</div>`;
      html += `<li><div class="home">${home ? cell : ""}</div><div class="min">${e.minute ?? ""}'</div><div class="away">${home ? "" : cell}</div></li>`;
    }
    return `<ul class="timeline">${html}</ul>`;
  }

  function lineupTable(g, seo) {
    const ps = (g.players || []).filter((p) => p.team === seo);
    if (!ps.length) return `<p class="muted">No lineup published.</p>`;
    const row = (p) => `<tr class="${p.starter ? "" : p.played ? "sub-row" : "dnp"}">
      <td class="num">${esc(p.number)}</td>
      <td class="l"><a href="#/player/${encodeURIComponent(p.player)}">${esc(p.first_name)} ${esc(p.last_name)}</a></td>
      <td class="l hide-sm muted">${esc((p.position || "").slice(0, 3))}</td>
      <td>${p.played ? p.minutes ?? "" : ""}</td><td>${p.goals || ""}</td><td>${p.assists || ""}</td>
      <td>${p.played ? p.shots : ""}</td><td>${p.played ? p.shots_on_goal : ""}</td>
      <td>${p.yellow ? `<span class="card-y" style="display:inline-block" aria-label="Yellow card"></span>` : ""}${p.red ? `<span class="card-r" style="display:inline-block" aria-label="Red card"></span>` : ""}</td>
    </tr>`;
    const starters = ps.filter((p) => p.starter), subs = ps.filter((p) => !p.starter && p.played), dnp = ps.filter((p) => !p.played);
    const keepers = ps.filter((p) => p.is_keeper && p.played);
    return `<div class="tablewrap"><table>
      <thead><tr><th>#</th><th class="l">${starters.length ? "Starters" : "Players"}</th><th class="l hide-sm">Pos</th><th title="Minutes">Min</th><th title="Goals">G</th><th title="Assists">A</th><th title="Shots">Sh</th><th title="Shots on goal">SoG</th><th><span class="sr-only" style="position:absolute;left:-9999px">Cards</span></th></tr></thead>
      <tbody>${starters.length ? `${starters.map(row).join("")}
      ${subs.length ? `<tr><td colspan="9" class="subhead l">Substitutes</td></tr>${subs.map(row).join("")}` : ""}
      ${dnp.length ? `<tr><td colspan="9" class="subhead l">Did not play</td></tr>${dnp.map(row).join("")}` : ""}`
      : subs.length ? `${subs.map(row).join("")}${dnp.length ? `<tr><td colspan="9" class="subhead l">Others on the roster</td></tr>${dnp.map(row).join("")}` : ""}`
      : `<tr><td colspan="9" class="subhead l">The school didn't publish who played. Roster:</td></tr>${dnp.map(row).join("")}`}
      </tbody></table></div>
      ${keepers.length ? `<div class="tablewrap"><table><thead><tr><th class="l">Goalkeeper</th><th>Min</th><th>Saves</th><th>GA</th></tr></thead><tbody>
        ${keepers.map((k) => `<tr><td class="l">${esc(k.first_name)} ${esc(k.last_name)}</td><td>${k.minutes ?? ""}</td><td>${k.saves ?? ""}</td><td>${k.goals_allowed ?? ""}</td></tr>`).join("")}
      </tbody></table></div>` : ""}`;
  }

  async function viewGame(id) {
    setNav("");
    await teams();
    let tab = "summary";
    const draw = async (fresh) => {
      const g = await load(`games/${id}.json`, { fresh });
      if (!g) { $main.innerHTML = `<div class="empty-state">This match isn't in the database. <a href="#/">Back to scores</a></div>`; return; }
      const H = team(g.h), A = team(g.a);
      const live = g.s === "live", started = g.s === "live" || g.s === "final";
      const goals = (g.events || []).filter((e) => e.type === "goal");
      const scorer = (seo) => goals.filter((e) => e.team === seo).map((e) => `${esc(e.player)} ${e.minute ?? ""}'`).join(", ");
      const when = g.t ? `${new Date(g.t * 1000).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric", year: "numeric" })}${g.tm ? `, ${localTime(g.t)}` : ""}` : "";
      const where = [g.venue, [g.city, g.st].filter(Boolean).join(", ")].filter(Boolean).join(" — ");
      const tabs = [["summary", "Summary"], ["lineups", "Lineups"], ["pbp", "Play-by-play"], ["h2h", "Head-to-head"]];

      let body = "";
      if (tab === "summary") {
        const stats = teamStatRows(g);
        body = `<div class="panel"><h3>Match events</h3>${started ? timelineHtml(g) : `<p class="muted">Kick-off ${esc(when)}.</p>`}</div>
          ${stats ? `<div class="panel" style="--hc:${esc(H.color)};--ac:${esc(A.color)}"><h3>Team stats</h3>${stats}</div>` : ""}`;
      } else if (tab === "lineups") {
        body = `<div class="lineups">
          <div class="panel"><h3>${kit(g.h)} ${esc(H.name)}</h3>${lineupTable(g, g.h)}</div>
          <div class="panel"><h3>${kit(g.a)} ${esc(A.name)}</h3>${lineupTable(g, g.a)}</div></div>`;
      } else if (tab === "pbp") {
        const key = /goal by|card|penalty/i;
        body = `<div class="panel">${(g.pbp || []).length ? `<ul class="pbp">${g.pbp.map((p) => `<li class="${key.test(p.x) ? "key" : ""}"><span class="c">${esc(p.c)}</span><span>${esc(p.x)}</span></li>`).join("")}</ul>` : `<p class="muted">No play-by-play for this match.</p>`}</div>`;
      } else {
        body = `<div class="group">${(g.h2h || []).length ? `<h2>Other meetings this season <span></span></h2>${g.h2h.map(matchRow).join("")}` : `<div class="empty-state" style="border:0">These teams haven't met elsewhere this season.</div>`}</div>`;
      }

      $main.innerHTML = `
        <section class="scoreboard" style="--hc:${esc(H.color)};--ac:${esc(A.color)}">
          <a class="sb-team" href="#/team/${esc(g.h)}">${kit(g.h, true)}<h1>${g.hr ? `<span class="rank">${g.hr}</span> ` : ""}${esc(H.name)}</h1><p>${esc(g.hrec || `${H.w}-${H.l}-${H.t}`)}</p></a>
          <div class="sb-score">
            <div class="big">${started ? `${g.hs ?? 0}<span>–</span>${g.as ?? 0}` : "vs"}</div>
            <div class="st${live ? " live" : ""}">${live ? `Live ${esc(statusText(g))}` : g.s === "final" ? statusText(g) : esc(statusText(g))}</div>
          </div>
          <a class="sb-team" href="#/team/${esc(g.a)}">${kit(g.a, true)}<h1>${g.ar ? `<span class="rank">${g.ar}</span> ` : ""}${esc(A.name)}</h1><p>${esc(g.arec || `${A.w}-${A.l}-${A.t}`)}</p></a>
          ${goals.length ? `<div class="scorers"><div>${scorer(g.h)}</div><div>${scorer(g.a)}</div></div>` : ""}
          <div class="sb-meta">${esc(when)}${where ? `<br>${esc(where)}` : ""}${g.rd ? `<br>${esc(g.rd)}` : ""}</div>
        </section>
        <div class="tabs" role="tablist">${tabs.map(([k, l]) => `<button role="tab" data-tab="${k}" aria-selected="${tab === k}">${l}</button>`).join("")}</div>
        ${body}`;
      $main.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => { tab = b.dataset.tab; draw(false); }));
      autoRefresh(live || (g.s === "pre" && g.t && g.t * 1000 - Date.now() < 3600e3) ? () => draw(true) : null);
    };
    $main.innerHTML = `<p class="loading">Loading match…</p>`;
    await draw(true);
  }

  // ---------------------------------------------------------------- team view
  async function viewTeam(seo) {
    setNav("");
    await teams();
    const d = await load(`teams/${seo}.json`, { fresh: true });
    if (!d) { $main.innerHTML = `<div class="empty-state">Team not found. <a href="#/">Back to scores</a></div>`; autoRefresh(null); return; }
    const t = d.team;
    let tab = "matches", sortKey = "goals";
    const played = d.games.filter((g) => g.s === "final");
    const upcoming = d.games.filter((g) => g.s !== "final");
    const resultFor = (g) => g.hs === g.as ? "T" : (g.h === seo) === (g.hs > g.as) ? "W" : "L";

    const draw = () => {
      let body = "";
      if (tab === "matches") {
        const row = (g) => {
          const home = g.h === seo, opp = home ? g.a : g.h;
          return `<tr><td class="l muted">${esc(shortDate(g))}</td><td class="l">${home ? "vs" : "@"}</td>
            <td class="l"><a href="#/team/${esc(opp)}" style="display:inline-flex;gap:8px;align-items:center">${kit(opp)}${esc(team(opp).name)}</a></td>
            <td>${g.s === "final" ? `<span class="res ${resultFor(g)}">${resultFor(g)}</span>` : ""}</td>
            <td><a href="#/game/${g.id}">${g.s === "final" || g.s === "live" ? `${home ? g.hs : g.as}–${home ? g.as : g.hs}` : esc(statusText(g))}</a></td></tr>`;
        };
        body = `${upcoming.length ? `<div class="panel"><h3>Upcoming</h3><div class="tablewrap"><table><tbody>${upcoming.map(row).join("")}</tbody></table></div></div>` : ""}
          <div class="panel"><h3>Results</h3>${played.length ? `<div class="tablewrap"><table><tbody>${played.slice().reverse().map(row).join("")}</tbody></table></div>` : `<p class="muted">No results yet.</p>`}</div>`;
      } else {
        const cols = [["gp", "GP"], ["gs", "GS"], ["minutes", "Min"], ["goals", "G"], ["assists", "A"], ["shots", "Sh"], ["shots_on_goal", "SoG"], ["yellow", "YC"], ["red", "RC"]];
        const squad = d.squad.slice().sort((a, b) => (b[sortKey] || 0) - (a[sortKey] || 0) || a.last.localeCompare(b.last));
        const gks = d.squad.filter((p) => p.gk && p.minutes);
        body = `<div class="panel"><div class="tablewrap"><table>
          <thead><tr><th>#</th><th class="l">Player</th><th class="l hide-sm">Pos</th>${cols.map(([k, l]) => `<th><button data-sort="${k}" style="border:0;background:none;cursor:pointer;color:${sortKey === k ? "var(--ink)" : "inherit"};font-weight:${sortKey === k ? 700 : 500}">${l}</button></th>`).join("")}</tr></thead>
          <tbody>${squad.map((p) => `<tr><td class="num">${esc(p.num)}</td><td class="l"><a href="#/player/${encodeURIComponent(p.key)}">${esc(p.first)} ${esc(p.last)}</a></td><td class="l hide-sm muted">${esc((p.pos || "").slice(0, 3))}</td>${cols.map(([k]) => `<td>${p[k] ?? 0}</td>`).join("")}</tr>`).join("")}</tbody>
        </table></div></div>
        ${gks.length ? `<div class="panel"><h3>Goalkeepers</h3><div class="tablewrap"><table><thead><tr><th class="l">Player</th><th>GP</th><th>Min</th><th>Saves</th><th>GA</th></tr></thead><tbody>${gks.map((p) => `<tr><td class="l"><a href="#/player/${encodeURIComponent(p.key)}">${esc(p.first)} ${esc(p.last)}</a></td><td>${p.gp}</td><td>${p.minutes}</td><td>${p.saves}</td><td>${p.goals_allowed}</td></tr>`).join("")}</tbody></table></div></div>` : ""}`;
      }
      $main.innerHTML = `
        <section class="hero" style="--tc:${esc(t.color || "#888")}">
          ${kit(seo, true)}
          <div>
            <h1>${esc(t.name)}</h1>
            <p>${esc([t.nick, t.full].filter(Boolean).join(", "))}</p>
            <p class="muted">${esc(divLabel(t.div))}${t.conf ? `, ${esc(confName(t.conf))}` : ""}</p>
            <div class="facts">
              <div><b>${t.w}-${t.l}-${t.t}</b><small>Record (W-L-T)</small></div>
              <div><b>${t.gf}</b><small>Goals for</small></div>
              <div><b>${t.ga}</b><small>Goals against</small></div>
              ${t.form?.length ? `<div><span class="form">${t.form.map((r) => `<i class="${r}">${r}</i>`).join("")}</span><small>Last ${t.form.length}</small></div>` : ""}
            </div>
          </div>
        </section>
        <div class="tabs" role="tablist">${[["matches", "Matches"], ["squad", "Squad stats"]].map(([k, l]) => `<button role="tab" data-tab="${k}" aria-selected="${tab === k}">${l}</button>`).join("")}</div>
        ${body}`;
      $main.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => { tab = b.dataset.tab; draw(); }));
      $main.querySelectorAll("[data-sort]").forEach((b) => b.addEventListener("click", () => { sortKey = b.dataset.sort; draw(); }));
    };
    draw();
    autoRefresh(null);
  }

  // ---------------------------------------------------------------- player view
  async function viewPlayer(key) {
    setNav("");
    await teams();
    const p = await load(`players/${encodeURIComponent(key)}.json`);
    autoRefresh(null);
    if (!p) { $main.innerHTML = `<div class="empty-state">Player not found. <a href="#/">Back to scores</a></div>`; return; }
    const T = team(p.team), tot = p.totals;
    $main.innerHTML = `
      <section class="hero" style="--tc:${esc(T.color || "#888")}">
        ${kit(p.team, true)}
        <div>
          <h1>${esc(p.first)} ${esc(p.last)}</h1>
          <p>${p.num ? `#${esc(p.num)}, ` : ""}${esc((p.pos || "").toLowerCase().replace(/^\w/, (c) => c.toUpperCase()))}${p.pos ? ", " : ""}<a href="#/team/${esc(p.team)}">${esc(T.name)}</a></p>
          <div class="facts">
            <div><b>${tot.gp}</b><small>Games</small></div><div><b>${tot.gs}</b><small>Starts</small></div>
            <div><b>${tot.minutes}</b><small>Minutes</small></div>
            ${p.gk ? `<div><b>${tot.saves}</b><small>Saves</small></div><div><b>${tot.goals_allowed}</b><small>Goals against</small></div>`
                   : `<div><b>${tot.goals}</b><small>Goals</small></div><div><b>${tot.assists}</b><small>Assists</small></div><div><b>${tot.shots}</b><small>Shots</small></div>`}
          </div>
        </div>
      </section>
      <div class="panel" style="margin-top:12px"><h3>Game log</h3><div class="tablewrap"><table>
        <thead><tr><th class="l">Date</th><th class="l">Opponent</th><th>Result</th><th>Min</th>${p.gk ? "<th>Saves</th><th>GA</th>" : "<th>G</th><th>A</th><th>Sh</th><th>SoG</th>"}<th>YC</th></tr></thead>
        <tbody>${p.log.slice().reverse().map((r) => {
          const g = r.g, opp = r.home ? g.a : g.h, my = r.home ? g.hs : g.as, th = r.home ? g.as : g.hs;
          const res = g.s === "final" ? (my === th ? "T" : my > th ? "W" : "L") : "";
          return `<tr class="${r.played ? "" : "dnp"}"><td class="l muted">${esc(shortDate(g))}</td>
            <td class="l">${r.home ? "vs" : "@"} <a href="#/team/${esc(opp)}">${esc(team(opp).name)}</a></td>
            <td><a href="#/game/${g.id}">${res ? `<span class="res ${res}">${res}</span> ` : ""}${my ?? ""}–${th ?? ""}</a></td>
            <td>${r.played ? r.minutes ?? "" : "DNP"}</td>
            ${p.gk ? `<td>${r.saves ?? ""}</td><td>${r.goals_allowed ?? ""}</td>` : `<td>${r.goals || ""}</td><td>${r.assists || ""}</td><td>${r.played ? r.shots : ""}</td><td>${r.played ? r.shots_on_goal : ""}</td>`}
            <td>${r.yellow || ""}</td></tr>`;
        }).join("")}</tbody></table></div></div>`;
  }

  // ---------------------------------------------------------------- standings
  async function viewStandings(params) {
    setNav("standings");
    autoRefresh(null);
    await teams();
    const div = params.get("div") || "1";
    const list = Object.values(TEAMS).filter((t) => String(t.div) === div && t.w + t.l + t.t > 0);
    const confs = new Map();
    for (const t of list) { const c = confName(t.conf); if (!confs.has(c)) confs.set(c, []); confs.get(c).push(t); }
    const pts = (t) => t.w * 3 + t.t;
    const sorted = [...confs.entries()].sort((a, b) => a[0].localeCompare(b[0]));
    $main.innerHTML = `
      <div class="filters"><div class="seg" role="group" aria-label="Division">
        ${DIV_TABS.map(([v, l]) => `<button data-div="${v}" aria-pressed="${div === v}">${l}</button>`).join("")}
      </div><span class="muted">Overall records, all games this season</span></div>
      ${sorted.map(([c, ts]) => `<section class="group"><h2>${esc(c)} <span>${ts.length} teams</span></h2><div class="tablewrap"><table>
        <thead><tr><th class="l" style="padding-left:14px">Team</th><th>GP</th><th>W</th><th>L</th><th>T</th><th class="hide-sm">GF</th><th class="hide-sm">GA</th><th>GD</th><th>Pts</th><th class="l hide-sm">Form</th></tr></thead>
        <tbody>${ts.sort((a, b) => pts(b) - pts(a) || (b.gf - b.ga) - (a.gf - a.ga) || b.gf - a.gf).map((t) => `<tr>
          <td class="l" style="padding-left:14px"><a href="#/team/${esc(t.seo)}" style="display:inline-flex;gap:8px;align-items:center">${kit(t.seo)}${esc(t.name)}</a></td>
          <td>${t.w + t.l + t.t}</td><td>${t.w}</td><td>${t.l}</td><td>${t.t}</td><td class="hide-sm">${t.gf}</td><td class="hide-sm">${t.ga}</td><td>${t.gf - t.ga > 0 ? "+" : ""}${t.gf - t.ga}</td><td><b>${pts(t)}</b></td>
          <td class="l hide-sm"><span class="form">${(t.form || []).map((r) => `<i class="${r}">${r}</i>`).join("")}</span></td></tr>`).join("")}</tbody>
      </table></div></section>`).join("") || `<div class="empty-state">No results yet.</div>`}`;
    $main.querySelectorAll("[data-div]").forEach((b) => b.addEventListener("click", () => go(`#/standings?div=${b.dataset.div}`)));
  }

  // ---------------------------------------------------------------- leaders
  async function viewLeaders(params) {
    setNav("leaders");
    autoRefresh(null);
    await teams();
    const L = (await load("leaders.json")) || {};
    const div = params.get("div") || "1", cat = params.get("cat") || "goals";
    const cats = [["goals", "Goals"], ["assists", "Assists"], ["points", "Points"], ["saves", "Saves"]];
    const rows = (L[div] || {})[cat] || [];
    const val = (p) => cat === "points" ? 2 * p.goals + p.assists : p[cat];
    $main.innerHTML = `
      <div class="filters">
        <div class="seg" role="group" aria-label="Division">${DIV_TABS.map(([v, l]) => `<button data-div="${v}" aria-pressed="${div === v}">${l}</button>`).join("")}</div>
        <div class="seg" role="group" aria-label="Category">${cats.map(([v, l]) => `<button data-cat="${v}" aria-pressed="${cat === v}">${l}</button>`).join("")}</div>
      </div>
      ${cat === "points" ? `<p class="muted" style="margin:-4px 0 10px">Points: 2 per goal, 1 per assist (NCAA scoring).</p>` : ""}
      <div class="panel"><div class="tablewrap"><table>
        <thead><tr><th>#</th><th class="l">Player</th><th class="l">Team</th><th>GP</th>${cat === "saves" ? "<th>Min</th><th>GA</th>" : "<th>G</th><th>A</th>"}<th>${esc(cats.find((c) => c[0] === cat)[1])}</th></tr></thead>
        <tbody>${rows.map((p, i) => `<tr><td class="num">${i + 1}</td>
          <td class="l"><a href="#/player/${encodeURIComponent(p.key)}">${esc(p.first)} ${esc(p.last)}</a></td>
          <td class="l"><a href="#/team/${esc(p.team)}" style="display:inline-flex;gap:8px;align-items:center">${kit(p.team)}${esc(team(p.team).name)}</a></td>
          <td>${p.gp}</td>${cat === "saves" ? `<td>${p.minutes}</td><td>${p.goals_allowed}</td>` : `<td>${p.goals}</td><td>${p.assists}</td>`}<td><b>${val(p)}</b></td></tr>`).join("")}</tbody>
      </table></div></div>`;
    $main.querySelectorAll("[data-div]").forEach((b) => b.addEventListener("click", () => go(`#/leaders?div=${b.dataset.div}&cat=${cat}`)));
    $main.querySelectorAll("[data-cat]").forEach((b) => b.addEventListener("click", () => go(`#/leaders?div=${div}&cat=${b.dataset.cat}`)));
  }

  // ---------------------------------------------------------------- search
  const $q = document.getElementById("q"), $res = document.getElementById("results");
  $q.addEventListener("focus", async () => {
    if (!SEARCH) { await teams(); SEARCH = (await load("search.json")) || { players: [] }; }
  });
  $q.addEventListener("input", async () => {
    const q = $q.value.trim().toLowerCase();
    if (q.length < 2) { $res.hidden = true; return; }
    await teams();
    if (!SEARCH) SEARCH = (await load("search.json")) || { players: [] };
    const ts = Object.values(TEAMS).filter((t) => `${t.name} ${t.full || ""} ${t.nick || ""}`.toLowerCase().includes(q)).slice(0, 6);
    const ps = SEARCH.players.filter((p) => p[1].toLowerCase().includes(q)).slice(0, 8);
    $res.innerHTML = [
      ...ts.map((t) => `<li><a href="#/team/${esc(t.seo)}">${kit(t.seo)}${esc(t.name)}<small>Team</small></a></li>`),
      ...ps.map((p) => `<li><a href="#/player/${encodeURIComponent(p[0])}">${kit(p[2])}${esc(p[1])}<small>${esc(team(p[2]).name)}</small></a></li>`),
    ].join("") || `<li class="muted" style="padding:8px">Nothing matches "${esc($q.value)}".</li>`;
    $res.hidden = false;
  });
  $q.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { $res.hidden = true; $q.blur(); }
    if (e.key === "ArrowDown") { e.preventDefault(); $res.querySelector("a")?.focus(); }
  });
  $res.addEventListener("keydown", (e) => {
    const links = [...$res.querySelectorAll("a")], i = links.indexOf(document.activeElement);
    if (e.key === "ArrowDown") { e.preventDefault(); links[i + 1]?.focus(); }
    if (e.key === "ArrowUp") { e.preventDefault(); (links[i - 1] || $q).focus(); }
    if (e.key === "Escape") { $res.hidden = true; $q.focus(); }
  });
  document.addEventListener("click", (e) => { if (!e.target.closest(".search")) $res.hidden = true; });
  $res.addEventListener("click", (e) => { if (e.target.closest("a")) { $res.hidden = true; $q.value = ""; } });

  // ---------------------------------------------------------------- router
  function go(hash) { if (location.hash === hash) route(); else location.hash = hash; }

  async function route() {
    const raw = location.hash.slice(1) || "/";
    const [path, qs] = raw.split("?");
    const params = new URLSearchParams(qs || "");
    const parts = path.split("/").filter(Boolean).map(decodeURIComponent);
    try {
      if (parts[0] === "game") await viewGame(parts[1]);
      else if (parts[0] === "team") await viewTeam(parts[1]);
      else if (parts[0] === "player") await viewPlayer(parts[1]);
      else if (parts[0] === "standings") await viewStandings(params);
      else if (parts[0] === "leaders") await viewLeaders(params);
      else await viewScores(params);
    } catch (err) {
      console.error(err);
      $main.innerHTML = `<div class="empty-state">${esc(err.message)}. Check your connection and reload the page.</div>`;
    }
    if (!parts[0] || parts[0] !== "game") window.scrollTo(0, 0);
  }
  window.addEventListener("hashchange", () => { route(); });

  load("meta.json", { fresh: true }).then((m) => {
    if (m) document.getElementById("updated").textContent = `Last update ${new Date(m.updated).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}.`;
  }).catch(() => {});
  route();
})();

const $ = (s) => document.querySelector(s);
const esc = (t) => String(t ?? "").replace(/[&<>"]/g, c =>
  ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
const api = (p) => fetch(p).then(r => r.ok ? r.json() : Promise.reject(r.status));

let curDay = null;

async function status() {
  const s = await api("/api/status");
  curDay = s.day;
  const w = s.weather ? ` · ${esc(s.weather.icon || "")} ${s.weather.temp_f}°F ${esc(s.weather.label || "")}` : "";
  $("#status").innerHTML =
    `Day ${s.day} · ${esc(s.date)} · ${esc(s.season)}${w}${s.holiday ? ` · ${esc(s.holiday)}` : ""} · ` +
    `${esc(s.time)} · ${s.population} residents · ` +
    Object.entries(s.moods).map(([m, n]) => `${m} ${n}`).join(" · ");
  $("#feed-day").textContent = `day ${s.day}`;
  $("#chron-day").value = Math.max(1, s.day - 1);
}

async function feed() {
  const [rows, arcRes, festRes, potRes, expRes] = await Promise.all([
    api("/api/feed?limit=60"),
    api("/api/arcs?status=active&limit=6").catch(() => ({})),
    api("/api/festivals?limit=4").catch(() => ({})),
    api("/api/potlucks?limit=16").catch(() => ({})),
    api("/api/expansion?limit=20").catch(() => ({})),
  ]);
  const expBox = $("#feed-expansion");
  if (expBox) {
    const ex = expRes?.latest || (expRes?.expansions && expRes.expansions[0]) || null;
    expBox.hidden = !ex;
    if (ex) {
      const kindLabel = ex.kind === "wing_addition" ? "Wing Addition" : "New Landmark";
      expBox.innerHTML = `
        <div class="expansion-card">
          <div class="fest-head">
            <span>🏗️ <b>${esc(ex.place_name)}</b> <span class="muted">(${esc(ex.district)} · Year ${ex.year} · Day ${ex.day + 1})</span></span>
            <span class="badge-pass">${esc(kindLabel)}</span>
          </div>
          <div class="fest-meta">
            Sponsor: <b>${esc(ex.sponsor_name || "Town Council")}</b> ·
            Cost: ${money(ex.cost_cents)} · Builders paid: ${money(ex.builders_paid_cents)}
          </div>
          ${ex.summary ? `<div class="fest-note">${esc(ex.summary)}</div>` : ""}
        </div>`;
    } else {
      expBox.innerHTML = "";
    }
  }
  const potBox = $("#feed-potluck");
  if (potBox) {
    const p = potRes?.latest || (potRes?.potlucks && potRes.potlucks[0]) || null;
    potBox.hidden = !p;
    if (p) {
      const hostLink = p.host_agent_id
        ? `<a class="click-link" onclick="mvPick({id:${p.host_agent_id}})"><b>${esc(p.host_name || "#" + p.host_agent_id)}</b></a>`
        : `<span class="muted">Community</span>`;
      const modeBadge = p.weather_mode === "indoor_potluck"
        ? `<span class="muted">🏠 Indoor Potluck</span>`
        : `<span class="badge-pass">🌳 Outdoor Block Party</span>`;
      potBox.innerHTML = `
        <div class="potluck-card">
          <div class="fest-head">
            <span>🍲 <b>${esc(p.theme)}</b> <span class="muted">(${esc(p.district)} · Day ${p.day + 1})</span></span>
            <span>${modeBadge}</span>
          </div>
          <div class="fest-meta">
            📍 ${esc(p.venue_name || p.district)} · Host: ${hostLink} ·
            Signature dish: <b>${esc(p.signature_dish)}</b> ·
            ${p.attendees_count ?? 0} attending · ${money(p.supply_cents)} supply kitty
          </div>
          ${p.summary ? `<div class="fest-note">${esc(p.summary)}</div>` : ""}
        </div>`;
    } else {
      potBox.innerHTML = "";
    }
  }
  const festBox = $("#feed-festival");
  if (festBox) {
    const f = festRes?.active || (festRes?.festivals && festRes.festivals[0]) || null;
    festBox.hidden = !f;
    if (f) {
      const winnerLink = f.winner_agent_id
        ? ` · Winner: <a class="click-link" onclick="mvPick({id:${f.winner_agent_id}})"><b>${esc(f.winner_agent_name || "#" + f.winner_agent_id)}</b></a>`
        : "";
      const statusBadge = f.status === "active"
        ? `<span class="badge-pass">Live Now</span>`
        : `<span class="muted">${esc(f.status)}</span>`;
      festBox.innerHTML = `
        <div class="fest-card">
          <div class="fest-head">
            <span>🎪 <b>${esc(f.name)}</b> <span class="muted">(${esc(f.season)} · Year ${f.year})</span></span>
            <span>${statusBadge}</span>
          </div>
          <div class="fest-meta">
            📍 ${esc(f.venue_name || "Town Fairgrounds")} · Days ${f.start_day + 1}–${f.end_day + 1} ·
            ${f.attendance_count ?? 0} attending · ${money(f.revenue_cents)} booth/prize flow${winnerLink}
          </div>
          ${f.weather_note ? `<div class="fest-note">🌦️ ${esc(f.weather_note)}</div>` : ""}
          ${f.summary ? `<div class="fest-note">${esc(f.summary)}</div>` : ""}
        </div>`;
    } else {
      festBox.innerHTML = "";
    }
  }
  const arcs = (arcRes && arcRes.arcs) || [];
  const arcBox = $("#feed-arcs");
  if (arcBox) {
    arcBox.hidden = arcs.length === 0;
    arcBox.innerHTML = arcs.length ? `<h3>Active Town Storylines</h3><div class="arcs-cards">` +
      arcs.map(ar => {
        const ch = (ar.chapters || []).slice(-1)[0];
        const link = ar.primary_agent_id
          ? `<a class="click-link" onclick="mvPick({id:${ar.primary_agent_id}})">${esc(ar.primary_name || "Cast")}</a>`
          : "";
        return `<div class="arc-card">
          <div class="arc-meta"><span class="arc-kind">${esc(ar.kind.replace(/_/g, " "))}</span> · Day ${ar.started_day + 1}–${ar.last_day + 1} · ${(ar.chapters || []).length} ch</div>
          <div class="arc-title">${esc(ar.title)} ${link}</div>
          ${ch ? `<div class="arc-chap">${esc(ch.text)}</div>` : ""}
        </div>`;
      }).join("") + `</div>` : "";
  }
  $("#feed-list").innerHTML = rows.map(e =>
    `<li class="imp${e.importance}"><b>${e.kind}</b> ${esc(e.text)}
     <span class="t">t${e.tick_of_day}</span></li>`).join("");
}

async function venues() {
  const rows = await api("/api/venues");
  $("#venues-body").innerHTML = rows.map(v =>
    `<tr><td>${esc(v.name)}</td><td>${v.kind}</td><td>${esc(v.district)}</td>
     <td>${v.occupancy}/${v.capacity}</td></tr>`).join("");
}

async function residents(q = "") {
  const rows = await api("/api/residents?q=" + encodeURIComponent(q));
  $("#resident-list").innerHTML = rows.map(r =>
    `<div class="res" data-id="${r.id}">
       <b>${esc(r.name)}</b> ${r.age} · ${esc(r.occupation)}
       <span class="mood">${r.mood}</span> <i>${esc(r.place || "")}</i></div>`).join("");
  document.querySelectorAll(".res").forEach(el =>
    el.onclick = () => residentCard(el.dataset.id));
}

async function residentCard(id) {
  const r = await api("/api/resident/" + id);
  const a = r.agent, s = r.state || {};
  $("#resident-card").hidden = false;
  $("#resident-card").innerHTML = `
    ${a.avatar_path ? `<img class="avatar" src="${esc(a.avatar_path)}" alt="">` : ""}
    <h3>${esc(a.name)} <button class="mini" onclick="mvBond(${id})">bond wheel</button></h3>
    <p>${a.age} ${esc(a.sex)} · ${esc(a.occupation)} · ${esc(a.marital_status)}
       ${r.job ? `· works at ${esc(r.job.place)}` : "· unemployed"}
       ${r.owns && r.owns.length ? `· owns <b>${r.owns.map(b => esc(b.name)).join(", ")}</b>` : ""}
       · standing <b>${a.standing ?? 0}</b>
       · influence <b>${(a.influence ?? 0).toFixed(1)}</b></p>
    <p class="state">energy ${Math.round(s.energy ?? 0)} · hunger ${Math.round(s.hunger ?? 0)}
       · social ${Math.round(s.social ?? 0)} · fun ${Math.round(s.fun ?? 0)}
       · stress ${Math.round(s.stress ?? 0)} · vitality <b>${Math.round(s.vitality ?? 60)}</b> · mood <b>${esc(s.mood)}</b>
       · $${((s.money_cents ?? 0) / 100).toFixed(2)}</p>
    <p class="persona">${esc((a.persona || "").slice(0, 400))}</p>
    ${r.individual ? `
    <div class="ind-box">
      <div><b>Temperament:</b> <span class="badge-pass">${esc(r.individual.archetype)}</span>
        · <b>Chronotype:</b> ${esc(r.individual.chronotype)}
        · <b>Signature knack:</b> ${esc(r.individual.knack)}
        ${r.individual.favorite_place ? `· <b>Favorite spot:</b> ${esc(r.individual.favorite_place)}` : ""}</div>
      <div class="axes-row">
        ${Object.entries(r.individual.axes || {}).map(([k, v]) =>
          `<span class="axis-pill">${esc(k)}: <b>${v >= 0 ? "+" : ""}${Number(v).toFixed(2)}</b></span>`
        ).join(" ")}
      </div>
      ${r.individual.household_tradition ? `<div class="amb-row"><b>Household tradition:</b> ${esc(r.individual.household_tradition)}</div>` : ""}
      ${r.individual.mentor ? `<div class="amb-row"><b>Mentor:</b> ${esc(r.individual.mentor.name)} in <b>${esc(r.individual.mentor.craft)}</b> (${esc(r.individual.mentor.status)} · ${r.individual.mentor.lessons}/${r.individual.mentor.target_lessons} lessons)</div>` : ""}
      ${r.individual.apprentices && r.individual.apprentices.length ? `<div class="amb-row"><b>Apprentices:</b> ${r.individual.apprentices.map(ap => `${esc(ap.name)} (${esc(ap.craft)} · ${esc(ap.status)})`).join(", ")}</div>` : ""}
      ${r.individual.keepsakes && r.individual.keepsakes.length ? `<div class="amb-row"><b>Keepsakes &amp; Heirlooms:</b> ${r.individual.keepsakes.map(k => `${esc(k.title)} <span class="muted">(by ${esc(k.creator_name)}${k.generations ? ` · gen ${k.generations} heirloom` : ""})</span>`).join(", ")}</div>` : ""}
      ${r.individual.ribbons && r.individual.ribbons.length ? `<div class="amb-row"><b>Blue Ribbons Won:</b> ${r.individual.ribbons.map(rb => `<span class="badge-pass">${esc(rb.title)} (Day ${rb.day})</span>`).join(" ")}</div>` : ""}
      ${r.individual.diary && r.individual.diary.length ? `<div class="amb-row"><b>Personal Diary:</b> <i>"${esc(r.individual.diary[0].entry)}"</i></div>` : ""}
      ${r.individual.ambition ? `
      <div class="amb-row">
        <b>Ambition:</b> ${esc(r.individual.ambition.label)}
        <span class="muted">(${esc(r.individual.ambition.status)} · ${Math.round((r.individual.ambition.progress || 0) * 100)}%)</span>
        <div class="prog-bar"><div class="prog-fill" style="width:${Math.round((r.individual.ambition.progress || 0) * 100)}%"></div></div>
      </div>` : ""}
    </div>` : ""}
    ${r.family && (r.family.parents.length || r.family.children.length || r.family.siblings.length || r.family.spouse) ? `
    <h4>Family Lineage · Gen ${r.family.generation} (${esc(r.family.surname)})</h4>
    <ul>
      ${r.family.parents.length ? `<li><b>Parents:</b> ${r.family.parents.map(p => `<a class="click-link" onclick="mvPick({id:${p.id}})">${esc(p.name)}</a>${p.alive ? "" : " (dec.)"}`).join(", ")}</li>` : ""}
      ${r.family.spouse ? `<li><b>${esc(r.family.spouse.label)}:</b> <a class="click-link" onclick="mvPick({id:${r.family.spouse.id}})">${esc(r.family.spouse.name)}</a>${r.family.spouse.alive ? "" : " (dec.)"}</li>` : ""}
      ${r.family.siblings.length ? `<li><b>Siblings:</b> ${r.family.siblings.map(s => `<a class="click-link" onclick="mvPick({id:${s.id}})">${esc(s.name)}</a>${s.alive ? "" : " (dec.)"}`).join(", ")}</li>` : ""}
      ${r.family.children.length ? `<li><b>Children:</b> ${r.family.children.map(ch => `<a class="click-link" onclick="mvPick({id:${ch.id}})">${esc(ch.name)}</a> (${ch.age}${ch.alive ? "" : ", dec."})`).join(", ")}</li>` : ""}
    </ul>` : ''}
    ${r.debts && (r.debts.owes.length || r.debts.owed.length) ? `
    <h4>IOUs</h4>
    <ul>${r.debts.owes.map(d => `<li>owes ${esc(d.creditor)} — ${esc(d.kind)}</li>`).join("")}
        ${r.debts.owed.map(d => `<li>${esc(d.debtor)} owes them — ${esc(d.kind)}</li>`).join("")}</ul>` : ""}
    ${r.groups && r.groups.length ? `
    <h4>Belongs to</h4>
    <ul>${r.groups.map(g =>
      `<li><b>${esc(g.name)}</b> <span class="t">${esc(g.kind)} · ${esc(g.role)}`
      + `${g.venue ? ` · meets at ${esc(g.venue)}` : ""}</span></li>`).join("")}</ul>` : ""}
    <h4>Relationships</h4>
    <ul>${r.relationships.map(x =>
      `<li>${esc(x.name)} — ${x.label} (fam ${Math.round(x.familiarity)},
        aff ${Math.round(x.affinity)}, rom ${Math.round(x.romance)})</li>`).join("")}</ul>
    ${r.memories && r.memories.length ? `
    <h4>Memories</h4>
    <ul>${r.memories.map(m =>
      `<li class="mem${m.kind === "reflection" ? " refl" : ""}">
        <span class="t">d${m.day} · ${esc(m.kind)}</span> ${esc(m.text)}</li>`).join("")}</ul>` : ""}
    <h4>Recent</h4>
    <ul>${r.recent.map(e => `<li>${esc(e.text)}</li>`).join("")}</ul>`;
}

async function chronicle() {
  const day = $("#chron-day").value;
  $("#chron-text").textContent = "loading…";
  try {
    const r = await api("/api/chronicle/" + day);
    $("#chron-text").textContent = r.chronicle || "(no chronicle)";
    $("#narratives").innerHTML = r.narratives.map(n =>
      `<div class="narr"><b>${esc(n.source)}</b><p>${esc(n.text)}</p></div>`).join("");
  } catch { $("#chron-text").textContent = "nothing written for that day"; $("#narratives").innerHTML = ""; }
}

async function debts() {
  const all = $("#debts-all").checked ? "0" : "1";
  const rows = await api("/api/debts?open_only=" + all);
  $("#debts-list").innerHTML = rows.map(d =>
    `<li>${esc(d.debtor)} owes ${esc(d.creditor)} — ${esc(d.kind)}
     <span class="t">${d.repaid_tick ? "settled" : "open"} · t${d.created_tick}</span></li>`).join("");
  const loanRes = await api("/api/loans?status=" + ($("#debts-all").checked ? "all" : "active"));
  const loans = loanRes.loans || [];
  $("#loans-body").innerHTML = loans.length ? loans.map(l => {
    const statusCls = l.status === "repaid" ? "badge-pass" : (l.status === "defaulted" ? "badge-fail" : "muted");
    return `<tr>
      <td><a class="click-link" onclick="mvPick({id: ${l.borrower_id}})"><b>${esc(l.borrower_name)}</b></a></td>
      <td><a class="click-link" onclick="mvPick({id: ${l.lender_id}})">${esc(l.lender_name)}</a></td>
      <td>${esc(l.place_name || "household expenses")}</td>
      <td>${money(l.principal_cents)}</td>
      <td>${money(l.remaining_cents)}</td>
      <td><span class="${statusCls}">${esc(l.status)}</span></td>
    </tr>`;
  }).join("") : `<tr><td colspan="6" class="muted">No Credit Union loans matching filter</td></tr>`;
}

async function rels() {
  if (!window._bond) renderTownNetwork($("#bond-host"), "all");
  const rows = await api("/api/relationships?label=" + $("#rel-filter").value);
  $("#rels-list").innerHTML = rows.map(r =>
    `<li class="click" onclick="mvBond(${r.a_id})"><b>${r.label}</b>
     ${esc(r.a_name)} &amp; ${esc(r.b_name)}
     <span class="t">fam ${Math.round(r.familiarity)} aff ${Math.round(r.affinity)}
     rom ${Math.round(r.romance)}</span></li>`).join("");
}

window.mvTownNet = (filterKind) => {
  renderTownNetwork($("#bond-host"), filterKind || "all");
};

// open a resident's ego network on the Bonds tab
window.mvBond = (id) => {
  document.querySelector('[data-tab="rels"]').click();
  renderBond($("#bond-host"), id);
};

function showGazette(edition) {
  $("#gaz-text").textContent = edition.text;
  const byline = edition.publisher || "Gazette staff";
  const editor = edition.editor || byline;
  const line = edition.editorial_line || "community";
  const basis = edition.editorial_basis || "independent local paper";
  const credibility = Math.round((edition.credibility ?? 1) * 100);
  const readers = edition.readers;
  const reachNote = (readers == null) ? "" :
    ` Read by ${readers.toLocaleString()} adults — its reach is what gives the ` +
    `paper power over the town's opinion.`;
  $("#gaz-meta").textContent =
    `Owned by ${byline}; edited by ${editor}. ` +
    `Editorial line: ${line} — ${basis}. Credibility ${credibility}%. ` +
    `This is a resident's account, not the neutral event record.` + reachNote;
  const claims = edition.claims || [];
  $("#gaz-claims").hidden = claims.length === 0;
  $("#gaz-claims-list").innerHTML = claims.map(c =>
    `<li><b>${esc(c.claim)}</b><br><span class="t">${esc(c.verdict)}. ` +
    `Ledger: ${esc(c.observer_record || "source event unavailable")}</span></li>`).join("");
  const records = edition.observer_record || [];
  $("#gaz-record-wrap summary").textContent =
    `Neutral observer record — ${records.length} ledger events this week`;
  $("#gaz-record").innerHTML = records.map(e =>
    `<li><span class="t">day ${e.day} · ${esc(e.kind)} · importance ${e.importance}</span> ` +
    `${esc(e.text)}</li>`).join("");
}

async function gazette() {
  const r = await api("/api/newspaper");
  const sel = $("#gaz-week");
  if (!r.latest) {
    sel.innerHTML = "";
    $("#gaz-text").textContent = "No edition published yet — the first paper " +
      "prints at the end of week 1.";
    $("#gaz-meta").textContent = "";
    $("#gaz-record").innerHTML = "";
    return;
  }
  if (sel.options.length !== r.editions.length) {
    sel.innerHTML = r.editions.map(e =>
      `<option value="${e.week}">week ${e.week}</option>`).join("");
  }
  sel.value = String(r.editions[0].week);
  sel.onchange = () => gazetteWeek(sel.value);
  showGazette(r.latest);
}

async function gazetteWeek(week) {
  const r = await api("/api/newspaper?week=" + week);
  showGazette(r);
}

const money = (c) => "$" + ((c ?? 0) / 100).toLocaleString(undefined,
  { maximumFractionDigits: 0 });

async function economy() {
  const [r, mkt, trRes, regRes] = await Promise.all([
    api("/api/economy"),
    api("/api/market?days=14").catch(() => ({})),
    api("/api/transit?days=14").catch(() => ({})),
    api("/api/regional?days=14").catch(() => ({})),
  ]);
  const s = r.stats;
  $("#econ-stats").innerHTML =
    `<div class="stat"><b>${money(s.money_supply_cents)}</b><span>in circulation</span></div>
     <div class="stat"><b>${money(s.median_balance_cents)}</b><span>median wallet</span></div>
     <div class="stat"><b>${(s.unemployment * 100).toFixed(1)}%</b><span>unemployment</span></div>
     <div class="stat"><b>${Math.round(s.wage_index * 100)}%</b><span>wages vs baseline</span></div>
     <div class="stat"><b>${s.businesses_open}</b><span>open</span></div>
     <div class="stat"><b>${s.businesses_closed}</b><span>dark</span></div>
     <div class="stat"><b>${s.in_debt}</b><span>in debt</span></div>
     <div class="stat"><b>${money(s.town_purse_cents)}</b><span>town purse</span></div>
     <div class="stat"><b>${money(s.public_payroll_week_cents)}</b><span>public payroll /wk</span></div>`;
  const mktBox = $("#econ-market");
  if (mktBox) {
    const td = mkt?.today || (mkt?.history && mkt.history[0]) || null;
    mktBox.hidden = !td;
    if (td) {
      const cpi = Number(td.cpi_index ?? 100);
      const cpiCls = cpi >= 105 ? "cpi-high" : (cpi <= 95 ? "cpi-low" : "cpi-steady");
      mktBox.innerHTML = `
        <div class="market-row">
          <span class="cpi-badge ${cpiCls}">📊 CPI ${cpi.toFixed(1)}</span>
          <span class="stat"><b>${Number(td.food_mult ?? 1).toFixed(2)}x</b><span>Groceries</span></span>
          <span class="stat"><b>${Number(td.dining_mult ?? 1).toFixed(2)}x</b><span>Dining</span></span>
          <span class="stat"><b>${Number(td.goods_mult ?? 1).toFixed(2)}x</b><span>Goods</span></span>
          <span class="stat"><b>${Number(td.leisure_mult ?? 1).toFixed(2)}x</b><span>Leisure</span></span>
        </div>
        ${td.driver_note ? `<div class="muted market-note">${esc(td.driver_note)}</div>` : ""}`;
    } else {
      mktBox.innerHTML = "";
    }
  }
  const trBox = $("#econ-transit");
  if (trBox) {
    const tr = trRes?.today || (trRes?.history && trRes.history[0]) || null;
    trBox.hidden = !tr;
    if (tr) {
      const driverLink = tr.driver_agent_id
        ? `<a class="click-link" onclick="mvPick({id:${tr.driver_agent_id}})">${esc(tr.driver_name || "#" + tr.driver_agent_id)}</a>`
        : `<span class="muted">—</span>`;
      trBox.innerHTML = `
        <div class="market-row">
          <span class="cpi-badge cpi-steady">🚌 Transit Day ${tr.day + 1}</span>
          <span class="stat"><b>${tr.total_rides ?? 0}</b><span>rides (${tr.storm_rides ?? 0} storm)</span></span>
          <span class="stat"><b>${Number(tr.on_time_pct ?? 100).toFixed(1)}%</b><span>on-time</span></span>
          <span class="stat"><b>${money(tr.fare_revenue_cents ?? 0)}</b><span>fare revenue</span></span>
          <span class="stat"><b>${esc(tr.top_route || "Line 1")}</b><span>top route · driver ${driverLink}</span></span>
        </div>
        ${tr.note ? `<div class="muted market-note">${esc(tr.note)}</div>` : ""}`;
    } else {
      trBox.innerHTML = "";
    }
  }
  const regBox = $("#econ-regional");
  if (regBox) {
    const rg = regRes?.today || (regRes?.history && regRes.history[0]) || null;
    regBox.hidden = !rg;
    if (rg) {
      const envoyLink = rg.envoy_agent_id
        ? `<a class="click-link" onclick="mvPick({id:${rg.envoy_agent_id}})">${esc(rg.envoy_name || "#" + rg.envoy_agent_id)}</a>`
        : `<span class="muted">—</span>`;
      regBox.innerHTML = `
        <div class="market-row">
          <span class="cpi-badge cpi-steady">🚂 ${esc(rg.sister_town)}</span>
          <span class="stat"><b>${rg.visitors_count ?? 0}</b><span>sister-town visitors</span></span>
          <span class="stat"><b>${money(rg.trade_volume_cents ?? 0)}</b><span>trade volume</span></span>
          <span class="stat"><b>${money(rg.customs_cents ?? 0)}</b><span>customs</span></span>
          <span class="stat"><b>${esc(rg.top_export)}</b><span>export · envoy ${envoyLink}</span></span>
          <span class="stat"><b>${esc(rg.top_import)}</b><span>top import</span></span>
        </div>
        ${rg.summary ? `<div class="muted market-note">${esc(rg.summary)}</div>` : ""}`;
    } else {
      regBox.innerHTML = "";
    }
  }
  $("#econ-body").innerHTML = r.businesses.map(b =>
    `<tr><td>${esc(b.name)}${b.founded_tick != null ? ` <span class="muted">(new ${esc(b.concept || "")})</span>` : ""}</td>
     <td>${b.owner_id ? `<a class="click-link" onclick="mvPick({id: ${b.owner_id}})">${esc(b.owner_name)}</a>` : `<span class="muted">—</span>`}</td>
     <td>${esc(b.kind)}</td>
     <td class="${b.status === "closed" ? "closed" : ""}">${b.status}</td>
     <td>${money(b.balance_cents)}</td><td>${money(b.revenue_total)}</td>
     <td>${money(b.payroll_total)}</td><td>${(b.price_index * 100).toFixed(0)}%</td>
     <td>${Math.round(b.ema_traffic)}</td></tr>`).join("");
  $("#econ-districts").innerHTML = (r.districts || []).map(d =>
    `<tr><td>${esc(d.district)}</td><td>${money(d.rent_cents)}</td>
     <td>${d.households}</td><td>${d.residents}</td>
     <td>${money(d.median_savings_cents)}</td></tr>`).join("");
  $("#econ-days").innerHTML = r.series.map(d =>
    `<tr><td>${d.day + 1}</td><td>${money(d.revenue_cents)}</td>
     <td>${money(d.payroll_cents)}</td><td>${money(d.rent_cents)}</td>
     <td>${money(d.spending_cents)}</td><td>${money(d.money_supply_cents)}</td>
     <td>${(d.unemployment_bp / 100).toFixed(1)}%</td>
     <td>${d.businesses_closed}</td></tr>`).join("");
}

function formatPolicy(name, val) {
  if (name === "levy_rate" || name === "dividend_share" || name === "min_wage" || name === "pension") {
    return (val * 100).toFixed(1) + "%";
  }
  if (name === "rent_multiplier") {
    return (val * 100).toFixed(0) + "%";
  }
  return String(val);
}

async function council() {
  const r = await api("/api/council");
  const seats = r.seats || [];
  const policies = r.policies || {};
  const nextElection = r.next_election_day;
  const daysLeft = curDay != null && nextElection != null ? Math.max(0, nextElection - curDay) : null;

  $("#council-meta").textContent =
    `Next council election: Day ${nextElection ?? "?"}` +
    (daysLeft != null ? ` (${daysLeft} days away)` : "") +
    ` · 5 district seats · policy levers directly influence town books, rent, and wages.`;

  const pressInfo = r.press || {};
  const endMap = r.endorsements || pressInfo.endorsements || {};
  const endEntries = Object.entries(endMap);
  const endNote = endEntries.length
    ? ` Latest endorsements: ${endEntries.map(([d, n]) => `${d}: ${n}`).join(", ")}.`
    : "";
  if (pressInfo.readers != null) {
    $("#council-press").textContent =
      `The Gazette (${pressInfo.editorial_line || "no line"}) is read by ` +
      `${pressInfo.readers.toLocaleString()} of ${pressInfo.adults.toLocaleString()} adults ` +
      `(${Math.round((pressInfo.reach || 0) * 100)}% reach, credibility ` +
      `${Math.round((pressInfo.credibility || 0) * 100)}%). Reach is how far the paper ` +
      `can move the town's opinion — and a caught lie shrinks it.` + endNote;
  }

  const polKeys = Object.keys(policies).sort();
  const opinion = r.opinion || {};
  $("#council-opinion").innerHTML = polKeys.map(k => {
    const v = opinion[k] ?? 0;
    const cls = v >= 0 ? "op-up" : "op-down";
    return `<div class="op-row">
      <span class="op-label">${k.replace(/_/g, " ")}</span>
      <span class="op-bar"><span class="${cls}" style="width:${Math.abs(v) * 50}%"></span></span>
      <span class="muted">${v >= 0 ? "+" : ""}${v.toFixed(2)} — wants to ${v >= 0 ? "raise" : "lower"}</span>
    </div>`;
  }).join("");

  $("#council-policies").innerHTML = polKeys.map(k => {
    const p = policies[k];
    const label = k.replace(/_/g, " ");
    const isMoved = Math.abs(p.now - p.default) > 1e-6;
    return `<div class="stat${isMoved ? " active-policy" : ""}">
      <b>${formatPolicy(k, p.now)}</b>
      <span>${label}</span>
      <span class="muted">def ${formatPolicy(k, p.default)} · [${formatPolicy(k, p.low)} - ${formatPolicy(k, p.high)}]</span>
    </div>`;
  }).join("");

  $("#council-seats").innerHTML = seats.length ? seats.map(s =>
    `<tr>
      <td>${s.seat}</td>
      <td><a class="click-link" onclick="mvPick({id: ${s.agent_id}})"><b>${esc(s.name || "Vacant")}</b></a></td>
      <td>${esc(s.district || "—")}</td>
      <td>${s.backers ?? 0}</td>
      <td>${money(s.backers_wallet)}</td>
      <td>${((s.backers_unemployed ?? 0) * 100).toFixed(1)}%</td>
    </tr>`
  ).join("") : `<tr><td colspan="6" class="muted">No council seated yet</td></tr>`;

  const motions = r.motions || [];
  $("#council-motions").innerHTML = motions.length ? motions.map(m => {
    const pass = m.passed === 1;
    const badge = pass ? `<span class="badge-pass">Passed</span>` : `<span class="badge-fail">Rejected</span>`;
    const dir = m.direction > 0 ? "▲ Raise" : "▼ Lower";
    const swing = m.press_swing === 1
      ? ` <span class="t">· opinion swung it (${(m.support ?? 0).toFixed(2)})</span>` : "";
    return `<tr>
      <td>Day ${m.day + 1}</td>
      <td><b>${esc(m.policy)}</b></td>
      <td>${formatPolicy(m.policy, m.value)}</td>
      <td>${dir}</td>
      <td>${m.votes_for} for / ${m.votes_against} against</td>
      <td>${badge}${swing}</td>
    </tr>`;
  }).join("") : `<tr><td colspan="6" class="muted">No motions debated yet</td></tr>`;

  const boycotts = r.boycotts || [];
  if (boycotts.length) {
    $("#council-boycotts-wrap").hidden = false;
    $("#council-boycotts").innerHTML = boycotts.map(b =>
      `<li><b>${esc(b.group_name)}</b> boycotting <b>${esc(b.place_name)}</b> ` +
      `<span class="t">until Day ${b.until_day + 1}</span><br>` +
      `<span class="muted">${esc(b.reason || "commercial grievance")}</span></li>`
    ).join("");
  } else {
    $("#council-boycotts-wrap").hidden = true;
  }

  const influential = r.influential || [];
  $("#council-influential").innerHTML = influential.length ? influential.map(inf =>
    `<li class="click" onclick="mvPick({id: ${inf.id}})">
      <b>${esc(inf.name)}</b>
      <span class="t">influence <b>${Number(inf.influence).toFixed(1)}</b></span>
    </li>`
  ).join("") : `<li class="muted">No influence data</li>`;
}


async function individual() {
  const [r, dynRes, musRes, awRes, deedsRes] = await Promise.all([
    api("/api/individual"),
    api("/api/dynasties").catch(() => ({})),
    api("/api/museum?limit=24").catch(() => ({})),
    api("/api/awards?limit=20").catch(() => ({})),
    api("/api/deeds?limit=25").catch(() => ({})),
  ]);
  const chronos = r.chronotypes || {};
  const order = ["early_bird", "morning", "day_walker", "evening", "night_owl"];
  const labels = {
    early_bird: "Early Birds (-1h)",
    morning: "Morning Risers (-30m)",
    day_walker: "Day Walkers",
    evening: "Evening Strollers (+30m)",
    night_owl: "Night Owls (+1h)"
  };
  $("#ind-chronos").innerHTML = order.map(k =>
    `<div class="stat"><b>${chronos[k] ?? 0}</b><span>${labels[k] || k}</span></div>`
  ).join("") + `<div class="stat active-policy"><b>${r.ambitions_completed ?? 0}</b><span>ambitions fulfilled</span></div>` + `<div class="stat active-policy"><b>${r.mentorships_graduated ?? 0}</b><span>apprentices graduated</span></div>`;

  const favs = r.favorite_places || [];
  $("#ind-favs").innerHTML = favs.length ? favs.map(f =>
    `<tr><td><b>${esc(f.venue)}</b></td><td>${f.regulars}</td></tr>`
  ).join("") : `<tr><td colspan="2" class="muted">Regulars form after warm encounters</td></tr>`;

  const archs = Object.entries(r.top_archetypes || {});
  $("#ind-archetypes").innerHTML = archs.map(([k, n]) =>
    `<tr><td>${esc(k)}</td><td>${n}</td></tr>`
  ).join("");

  const evs = Object.entries(r.personal_event_counts || {});
  $("#ind-events").innerHTML = evs.length ? evs.map(([k, n]) =>
    `<tr><td><b>${esc(k.replace(/_/g, " "))}</b></td><td>${n}</td></tr>`
  ).join("") : `<tr><td colspan="2" class="muted">No personal happenings recorded yet</td></tr>`;

  const ambs = r.active_ambitions || [];
  $("#ind-ambitions").innerHTML = ambs.length ? ambs.map(am => {
    const pct = Math.round((am.progress || 0) * 100);
    return `<tr>
      <td><a class="click-link" onclick="mvPick({id: ${am.agent_id}})"><b>${esc(am.name)}</b></a></td>
      <td>${esc(am.occupation || "—")}</td>
      <td>${esc(am.label)}</td>
      <td><div class="prog-bar"><div class="prog-fill" style="width:${pct}%"></div></div> <span class="muted">${pct}%</span></td>
      <td>${esc(am.place_name || "home")}</td>
    </tr>`;
  }).join("") : `<tr><td colspan="5" class="muted">No active ambitions</td></tr>`;

  const ments = r.mentorships || [];
  $("#ind-mentorships").innerHTML = ments.length ? ments.map(m => {
    const pct = Math.round(((m.lessons || 0) / Math.max(1, m.target_lessons || 5)) * 100);
    const badge = m.status === "graduated"
      ? `<span class="badge-pass">Graduated</span>`
      : `<span class="muted">${m.lessons}/${m.target_lessons} lessons</span>`;
    return `<tr>
      <td><a class="click-link" onclick="mvPick({id: ${m.mentor_id}})"><b>${esc(m.mentor_name)}</b></a></td>
      <td><a class="click-link" onclick="mvPick({id: ${m.apprentice_id}})"><b>${esc(m.apprentice_name)}</b></a></td>
      <td><b>${esc(m.craft)}</b></td>
      <td>${esc(m.venue_name || "town")}</td>
      <td><div class="prog-bar"><div class="prog-fill" style="width:${pct}%"></div></div></td>
      <td>${badge}</td>
    </tr>`;
  }).join("") : `<tr><td colspan="6" class="muted">No mentorships formed yet</td></tr>`;

  const trads = r.household_traditions || [];
  $("#ind-traditions").innerHTML = trads.length ? trads.map(t =>
    `<tr><td><b>${esc(t.tradition)}</b></td><td>${t.households}</td><td>${t.residents}</td></tr>`
  ).join("") : `<tr><td colspan="3" class="muted">No household traditions yet</td></tr>`;

  const dyns = (dynRes && dynRes.dynasties) || [];
  $("#ind-dynasties").innerHTML = dyns.length ? dyns.map(d => {
    const notes = (d.notables || []).map(n =>
      `<a class="click-link" onclick="mvPick({id:${n.id}})">${esc(n.name)}</a>`
    ).join(", ");
    return `<tr>
      <td><b>${esc(d.surname)}</b></td>
      <td>${d.living}</td>
      <td>${d.deceased}</td>
      <td>Gen ${d.generations}</td>
      <td>${money(d.total_wealth_cents)}</td>
      <td>${notes || `<span class="muted">—</span>`}</td>
    </tr>`;
  }).join("") : `<tr><td colspan="6" class="muted">No multi-member families yet</td></tr>`;

  const keeps = r.keepsakes || [];
  $("#ind-keepsakes").innerHTML = keeps.length ? keeps.map(k =>
    `<tr>
      <td><b>${esc(k.title)}</b></td>
      <td>${esc(k.kind)}</td>
      <td><a class="click-link" onclick="mvPick({id: ${k.creator_id}})">${esc(k.creator_name)}</a></td>
      <td><a class="click-link" onclick="mvPick({id: ${k.owner_id}})"><b>${esc(k.owner_name)}</b></a></td>
      <td>${k.generations > 0 ? `<span class="badge-pass">Gen ${k.generations} Heirloom</span>` : `<span class="muted">Original</span>`}</td>
    </tr>`
  ).join("") : `<tr><td colspan="5" class="muted">No keepsakes crafted yet</td></tr>`;

  const nbs = r.neighborhoods || [];
  $("#ind-neighborhoods").innerHTML = nbs.length ? nbs.map(nb =>
    `<tr>
      <td><b>${esc(nb.district)}</b></td>
      <td>${nb.residents}</td>
      <td>${Math.round((nb.cohesion || 0) * 100)}%</td>
      <td>${Math.round((nb.vigilance || 0) * 100)}%</td>
      <td>${nb.last_event_kind ? `${esc(nb.last_event_kind.replace(/_/g, " "))} (Day ${nb.last_event_day + 1})` : `<span class="muted">—</span>`}</td>
    </tr>`
  ).join("") : `<tr><td colspan="5" class="muted">No district data</td></tr>`;

  const cult = r.cultural_commons || [];
  $("#ind-culture").innerHTML = cult.length ? cult.map(c =>
    `<tr>
      <td><b>${esc(c.title)}</b></td>
      <td>${esc(c.kind)}</td>
      <td><a class="click-link" onclick="mvPick({id: ${c.creator_id}})">${esc(c.creator_name)}</a></td>
      <td>${esc(c.venue_name || "town")}</td>
      <td><b>${c.appreciations ?? 0}</b></td>
    </tr>`
  ).join("") : `<tr><td colspan="5" class="muted">No works exhibited yet</td></tr>`;

  const mus = musRes || {};
  const musCats = Object.entries(mus.by_category || {})
    .map(([k, n]) => `<span class="cat-pill">${esc(k)}: <b>${n}</b></span>`).join(" ");
  const feat = mus.featured;
  $("#ind-museum-meta").innerHTML = `
    <div class="stat"><b>${mus.total_exhibits ?? 0}</b><span>curated exhibits</span></div>
    <div class="stat"><b>${mus.total_visitors ?? 0}</b><span>total visitors (${mus.today_visitors ?? 0} today)</span></div>
    <div class="stat"><b>${money(mus.total_donations_cents ?? 0)}</b><span>visitor donations</span></div>
    ${feat ? `<div class="stat active-policy museum-featured-card"><b>🏛️ ${esc(feat.title)}</b><span>Featured (${esc(feat.category)}) · ${feat.visitors_count ?? 0} visitors</span></div>` : ""}
    ${musCats ? `<div class="stat"><div>${musCats}</div><span>by category</span></div>` : ""}`;
  const exs = mus.exhibits || [];
  $("#ind-museum").innerHTML = exs.length ? exs.map(ex => {
    const subj = ex.subject_agent_id
      ? `<a class="click-link" onclick="mvPick({id: ${ex.subject_agent_id}})">${esc(ex.subject_name || "#" + ex.subject_agent_id)}</a>`
      : `<span class="muted">—</span>`;
    const donor = ex.donor_agent_id
      ? `<a class="click-link" onclick="mvPick({id: ${ex.donor_agent_id}})">${esc(ex.donor_name || "#" + ex.donor_agent_id)}</a>`
      : `<span class="muted">—</span>`;
    const badge = ex.featured ? ` <span class="badge-pass">Featured</span>` : "";
    return `<tr>
      <td><b>${esc(ex.title)}</b>${badge}<br><small class="muted">${esc(ex.description)}</small></td>
      <td>${esc(ex.category)}</td>
      <td>Yr ${ex.year_curated} · Day ${ex.day_curated + 1}</td>
      <td>${subj}</td>
      <td>${donor}</td>
      <td>${esc(ex.place_name || "Museum")}</td>
      <td><b>${ex.visitors_count ?? 0}</b></td>
    </tr>`;
  }).join("") : `<tr><td colspan="7" class="muted">No museum exhibits curated yet</td></tr>`;

  const aw = awRes || {};
  const awCats = Object.entries(aw.by_category || {})
    .map(([k, n]) => `<span class="cat-pill">${esc(k)}: <b>${n}</b></span>`).join(" ");
  $("#ind-awards-meta").innerHTML = `
    <div class="stat"><b>${aw.total_awards ?? 0}</b><span>civic honours awarded</span></div>
    <div class="stat"><b>${money(aw.total_stipends_cents ?? 0)}</b><span>total stipends</span></div>
    ${aw.latest_season ? `<div class="stat active-policy"><b>🏅 ${esc(aw.latest_season)} Yr ${aw.latest_year}</b><span>latest ceremony (Day ${(aw.latest_day ?? 0) + 1})</span></div>` : ""}
    ${awCats ? `<div class="stat"><div>${awCats}</div><span>by category</span></div>` : ""}`;
  const awList = aw.awards || [];
  $("#ind-awards").innerHTML = awList.length ? awList.map(a => {
    const rec = a.recipient_agent_id
      ? `<a class="click-link" onclick="mvPick({id: ${a.recipient_agent_id}})"><b>${esc(a.recipient_name || "#" + a.recipient_agent_id)}</b></a>`
      : `<span class="muted">—</span>`;
    const stipend = (a.stipend_cents ?? 0) > 0
      ? `<span class="badge-pass">${money(a.stipend_cents)}</span>`
      : `<span class="muted">Honorary</span>`;
    return `<tr>
      <td>${esc(a.season)} Yr ${a.year}</td>
      <td><b>${esc(a.category)}</b></td>
      <td>${rec}</td>
      <td>${esc(a.business_name || "—")}</td>
      <td>${stipend}</td>
      <td>${esc(a.citation)}</td>
    </tr>`;
  }).join("") : `<tr><td colspan="6" class="muted">No seasonal awards conferred yet</td></tr>`;

  const dd = deedsRes || {};
  $("#ind-deeds-meta").innerHTML = `
    <div class="stat"><b>${dd.total_deeds ?? 0}</b><span>home deeds</span></div>
    <div class="stat"><b>${dd.active_mortgages ?? 0}</b><span>active mortgages</span></div>
    <div class="stat"><b>${money(dd.total_mortgage_cents ?? 0)}</b><span>mortgage balance</span></div>
    <div class="stat active-policy"><b>Gen ${dd.max_generation ?? 0}</b><span>oldest dynasty home</span></div>`;
  const deedsList = dd.deeds || [];
  $("#ind-deeds").innerHTML = deedsList.length ? deedsList.map(d => {
    const owner = d.owner_agent_id
      ? `<a class="click-link" onclick="mvPick({id: ${d.owner_agent_id}})"><b>${esc(d.owner_name || "#" + d.owner_agent_id)}</b></a>`
      : `<span class="muted">Unclaimed</span>`;
    const genBadge = (d.generation ?? 1) > 1
      ? `<span class="badge-pass">Gen ${d.generation} Heirloom</span>`
      : `<span class="muted">Gen ${d.generation ?? 1}</span>`;
    const mort = (d.mortgage_cents ?? 0) > 0
      ? money(d.mortgage_cents)
      : `<span class="badge-pass">Paid Off</span>`;
    return `<tr>
      <td><b>${esc(d.place_name || "#" + d.place_id)}</b></td>
      <td>${esc(d.district || "—")}</td>
      <td>${owner}</td>
      <td>${esc(d.surname || "—")}</td>
      <td>${genBadge}</td>
      <td>${money(d.price_cents)}</td>
      <td>${mort}</td>
      <td><small class="muted">${esc(d.note || "")}</small></td>
    </tr>`;
  }).join("") : `<tr><td colspan="8" class="muted">No home deeds recorded yet</td></tr>`;

  const projs = r.civic_projects || [];
  $("#ind-projects").innerHTML = projs.length ? projs.map(p => {
    const fpct = Math.min(100, Math.round(((p.funded_cents || 0) / Math.max(1, p.target_cents || 35000)) * 100));
    const badge = p.status === "completed"
      ? `<span class="badge-pass">Completed</span>`
      : (p.status === "active" ? `<b>Active</b>` : `<span class="muted">Proposed</span>`);
    return `<tr>
      <td><b>${esc(p.title)}</b></td>
      <td>${esc(p.district)}</td>
      <td>${esc(p.venue_name || "—")}</td>
      <td><div class="prog-bar"><div class="prog-fill" style="width:${fpct}%"></div></div> <span class="muted">$${Math.round((p.funded_cents || 0)/100)}/$${Math.round((p.target_cents || 0)/100)}</span></td>
      <td>${p.volunteer_hours}/${p.target_hours} hrs</td>
      <td>${badge} <span class="muted">${esc(p.benefit)}</span></td>
    </tr>`;
  }).join("") : `<tr><td colspan="6" class="muted">No civic projects</td></tr>`;

  const hl = r.health || {};
  $("#ind-health").innerHTML = `
    <div class="stat"><b>${hl.avg_vitality ?? 60}</b><span>mean vitality</span></div>
    <div class="stat"><b>${hl.high_vitality ?? 0}</b><span>high vitality (&ge;75)</span></div>
    <div class="stat"><b>${hl.currently_sick ?? 0}</b><span>currently sick</span></div>
    <div class="stat"><b>${hl.hospital_clinicians ?? 0}</b><span>hospital clinicians</span></div>
    <div class="stat active-policy"><b>${hl.treatments_total ?? 0}</b><span>patients treated</span></div>
    <div class="stat"><b>${hl.checkups_total ?? 0}</b><span>wellness checkups</span></div>
  `;

  const comps = r.competitions || [];
  $("#ind-competitions").innerHTML = comps.length ? comps.map(cp =>
    `<tr>
      <td>Day ${cp.day}</td>
      <td><b>${esc(cp.title)}</b></td>
      <td><a class="click-link" onclick="mvPick({id: ${cp.winner_id}})"><b>${esc(cp.winner_name)}</b></a></td>
      <td>${esc(cp.winning_work || "—")}</td>
      <td>${cp.runner_up_id ? `<a class="click-link" onclick="mvPick({id: ${cp.runner_up_id}})">${esc(cp.runner_up_name)}</a>` : "—"}</td>
      <td>${cp.entrants}</td>
    </tr>`
  ).join("") : `<tr><td colspan="6" class="muted">No competitions held yet</td></tr>`;

  const drs = r.diaries || [];
  $("#ind-diaries").innerHTML = drs.length ? drs.map(d =>
    `<li><span class="t">Day ${d.day} · ${esc(d.kind)}</span> <a class="click-link" onclick="mvPick({id: ${d.agent_id}})"><b>${esc(d.name)}</b></a>: <i>"${esc(d.entry)}"</i></li>`
  ).join("") : `<li class="muted">No diary entries recorded yet</li>`;

  const mls = r.milestones || [];
  $("#ind-milestones").innerHTML = mls.length ? mls.map(m =>
    `<li><span class="t">Day ${m.day} · ${esc(m.tag.replace(/_/g, " "))}</span> ${esc(m.text)}</li>`
  ).join("") : `<li class="muted">No personal milestones recorded yet</li>`;
}

const loaders = { feed, venues, residents: () => residents($("#q").value), individual, chronicle, rels, debts, gazette, economy, council,
  map: () => renderMap($("#map-host")) };
for (const b of document.querySelectorAll("#tabs button"))
  b.onclick = () => {
    document.querySelectorAll("#tabs button").forEach(x => x.classList.remove("on"));
    document.querySelectorAll(".panel").forEach(x => x.classList.remove("on"));
    b.classList.add("on");
    $("#" + b.dataset.tab).classList.add("on");
    loaders[b.dataset.tab]();
  };

// clicking a map dot jumps to that resident's card
window.mvPick = (agent) => {
  document.querySelector('[data-tab="residents"]').click();
  residentCard(agent.id);
};

$("#q").oninput = (e) => residents(e.target.value);
$("#chron-go").onclick = chronicle;
$("#rel-filter").onchange = rels;
$("#debts-all").onchange = debts;

status().then(feed);
setInterval(async () => {
  await status();
  if ($("#feed").classList.contains("on")) feed();
  if ($("#map").classList.contains("on") && window._map) window._map.refresh();
}, 5000);

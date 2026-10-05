const $ = (s) => document.querySelector(s);
const esc = (t) => String(t ?? "").replace(/[&<>"]/g, c =>
  ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
const api = (p) => fetch(p).then(r => r.ok ? r.json() : Promise.reject(r.status));

let curDay = null;

async function status() {
  const s = await api("/api/status");
  curDay = s.day;
  $("#status").innerHTML =
    `Day ${s.day} · ${esc(s.date)} · ${esc(s.season)}${s.holiday ? ` · ${esc(s.holiday)}` : ""} · ` +
    `${esc(s.time)} · ${s.population} residents · ` +
    Object.entries(s.moods).map(([m, n]) => `${m} ${n}`).join(" · ");
  $("#feed-day").textContent = `day ${s.day}`;
  $("#chron-day").value = Math.max(1, s.day - 1);
}

async function feed() {
  const rows = await api("/api/feed?limit=60");
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
       · standing <b>${a.standing ?? 0}</b></p>
    <p class="state">energy ${Math.round(s.energy ?? 0)} · hunger ${Math.round(s.hunger ?? 0)}
       · social ${Math.round(s.social ?? 0)} · fun ${Math.round(s.fun ?? 0)}
       · stress ${Math.round(s.stress ?? 0)} · mood <b>${esc(s.mood)}</b>
       · $${((s.money_cents ?? 0) / 100).toFixed(2)}</p>
    <p class="persona">${esc((a.persona || "").slice(0, 400))}</p>
    ${r.debts && (r.debts.owes.length || r.debts.owed.length) ? `
    <h4>IOUs</h4>
    <ul>${r.debts.owes.map(d => `<li>owes ${esc(d.creditor)} — ${esc(d.kind)}</li>`).join("")}
        ${r.debts.owed.map(d => `<li>${esc(d.debtor)} owes them — ${esc(d.kind)}</li>`).join("")}</ul>` : ""}
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
}

async function rels() {
  const rows = await api("/api/relationships?label=" + $("#rel-filter").value);
  $("#rels-list").innerHTML = rows.map(r =>
    `<li class="click" onclick="mvBond(${r.a_id})"><b>${r.label}</b>
     ${esc(r.a_name)} &amp; ${esc(r.b_name)}
     <span class="t">fam ${Math.round(r.familiarity)} aff ${Math.round(r.affinity)}
     rom ${Math.round(r.romance)}</span></li>`).join("");
}

// open a resident's ego network on the Bonds tab
window.mvBond = (id) => {
  document.querySelector('[data-tab="rels"]').click();
  renderBond($("#bond-host"), id);
};

async function gazette() {
  const r = await api("/api/newspaper");
  const sel = $("#gaz-week");
  if (!r.latest) {
    sel.innerHTML = "";
    $("#gaz-text").textContent = "No edition published yet — the first paper " +
      "prints at the end of week 1.";
    return;
  }
  if (sel.options.length !== r.editions.length) {
    sel.innerHTML = r.editions.map(e =>
      `<option value="${e.week}">week ${e.week}</option>`).join("");
  }
  sel.value = String(r.editions[0].week);
  sel.onchange = () => gazetteWeek(sel.value);
  $("#gaz-text").textContent = r.latest.text;
}

async function gazetteWeek(week) {
  const r = await api("/api/newspaper?week=" + week);
  $("#gaz-text").textContent = r.text;
}

const money = (c) => "$" + ((c ?? 0) / 100).toLocaleString(undefined,
  { maximumFractionDigits: 0 });

async function economy() {
  const r = await api("/api/economy");
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
  $("#econ-body").innerHTML = r.businesses.map(b =>
    `<tr><td>${esc(b.name)}</td><td>${esc(b.kind)}</td>
     <td class="${b.status === "closed" ? "closed" : ""}">${b.status}</td>
     <td>${money(b.balance_cents)}</td><td>${money(b.revenue_total)}</td>
     <td>${money(b.payroll_total)}</td><td>${(b.price_index * 100).toFixed(0)}%</td>
     <td>${Math.round(b.ema_traffic)}</td></tr>`).join("");
  $("#econ-days").innerHTML = r.series.map(d =>
    `<tr><td>${d.day + 1}</td><td>${money(d.revenue_cents)}</td>
     <td>${money(d.payroll_cents)}</td><td>${money(d.rent_cents)}</td>
     <td>${money(d.spending_cents)}</td><td>${money(d.money_supply_cents)}</td>
     <td>${(d.unemployment_bp / 100).toFixed(1)}%</td>
     <td>${d.businesses_closed}</td></tr>`).join("");
}

const loaders = { feed, venues, residents: () => residents($("#q").value), chronicle, rels, debts, gazette, economy,
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

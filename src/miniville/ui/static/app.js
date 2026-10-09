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
       ${r.owns && r.owns.length ? `· owns <b>${r.owns.map(b => esc(b.name)).join(", ")}</b>` : ""}
       · standing <b>${a.standing ?? 0}</b>
       · influence <b>${(a.influence ?? 0).toFixed(1)}</b></p>
    <p class="state">energy ${Math.round(s.energy ?? 0)} · hunger ${Math.round(s.hunger ?? 0)}
       · social ${Math.round(s.social ?? 0)} · fun ${Math.round(s.fun ?? 0)}
       · stress ${Math.round(s.stress ?? 0)} · mood <b>${esc(s.mood)}</b>
       · $${((s.money_cents ?? 0) / 100).toFixed(2)}</p>
    <p class="persona">${esc((a.persona || "").slice(0, 400))}</p>
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

function showGazette(edition) {
  $("#gaz-text").textContent = edition.text;
  const byline = edition.publisher || "Gazette staff";
  const editor = edition.editor || byline;
  const line = edition.editorial_line || "community";
  const basis = edition.editorial_basis || "independent local paper";
  const credibility = Math.round((edition.credibility ?? 1) * 100);
  $("#gaz-meta").textContent =
    `Owned by ${byline}; edited by ${editor}. ` +
    `Editorial line: ${line} — ${basis}. Credibility ${credibility}%. ` +
    `This is a resident's account, not the neutral event record.`;
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
    `<tr><td>${esc(b.name)}${b.founded_tick != null ? ` <span class="muted">(new ${esc(b.concept || "")})</span>` : ""}</td>
     <td>${b.owner_id ? `<a class="click-link" onclick="mvPick({id: ${b.owner_id}})">${esc(b.owner_name)}</a>` : `<span class="muted">—</span>`}</td>
     <td>${esc(b.kind)}</td>
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

  const polKeys = Object.keys(policies).sort();
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
    return `<tr>
      <td>Day ${m.day + 1}</td>
      <td><b>${esc(m.policy)}</b></td>
      <td>${formatPolicy(m.policy, m.value)}</td>
      <td>${dir}</td>
      <td>${m.votes_for} for / ${m.votes_against} against</td>
      <td>${badge}</td>
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

const loaders = { feed, venues, residents: () => residents($("#q").value), chronicle, rels, debts, gazette, economy, council,
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

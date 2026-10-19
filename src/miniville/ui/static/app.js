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
  if (pressInfo.readers != null) {
    $("#council-press").textContent =
      `The Gazette (${pressInfo.editorial_line || "no line"}) is read by ` +
      `${pressInfo.readers.toLocaleString()} of ${pressInfo.adults.toLocaleString()} adults ` +
      `(${Math.round((pressInfo.reach || 0) * 100)}% reach, credibility ` +
      `${Math.round((pressInfo.credibility || 0) * 100)}%). Reach is how far the paper ` +
      `can move the town's opinion — and a caught lie shrinks it.`;
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
  const r = await api("/api/individual");
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

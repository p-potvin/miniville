const $ = (s) => document.querySelector(s);
const esc = (t) => String(t ?? "").replace(/[&<>"]/g, c =>
  ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
const api = (p) => fetch(p).then(r => r.ok ? r.json() : Promise.reject(r.status));

let curDay = null;

async function status() {
  const s = await api("/api/status");
  curDay = s.day;
  $("#status").innerHTML =
    `Day ${s.day} · ${esc(s.time)} · ${s.population} residents · ` +
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
    <h3>${esc(a.name)}</h3>
    <p>${a.age} ${esc(a.sex)} · ${esc(a.occupation)} · ${esc(a.marital_status)}
       ${r.job ? `· works at ${esc(r.job.place)}` : "· unemployed"}</p>
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
    `<li><b>${r.label}</b> ${esc(r.a_name)} &amp; ${esc(r.b_name)}
     <span class="t">fam ${Math.round(r.familiarity)} aff ${Math.round(r.affinity)}
     rom ${Math.round(r.romance)}</span></li>`).join("");
}

const loaders = { feed, venues, residents: () => residents($("#q").value), chronicle, rels, debts };
for (const b of document.querySelectorAll("#tabs button"))
  b.onclick = () => {
    document.querySelectorAll("#tabs button").forEach(x => x.classList.remove("on"));
    document.querySelectorAll(".panel").forEach(x => x.classList.remove("on"));
    b.classList.add("on");
    $("#" + b.dataset.tab).classList.add("on");
    loaders[b.dataset.tab]();
  };
$("#q").oninput = (e) => residents(e.target.value);
$("#chron-go").onclick = chronicle;
$("#rel-filter").onchange = rels;
$("#debts-all").onchange = debts;

status().then(feed);
setInterval(async () => { await status(); if ($("#feed").classList.contains("on")) feed(); }, 5000);

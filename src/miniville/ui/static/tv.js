/* Miniville 10-Foot Living TV & Interactive Ambient Director Mode (v0.46-v0.47)
 *
 * Balances continuous, soothing background progression on a television screen
 * with instant interactive drill-down when the viewer touches the mouse/remote:
 * - Auto-Director Camera cycles every 12s across Spotlight Resident -> Spotlight
 *   Venue -> Full Town Panorama while highlighting only 8-10 active commuters.
 * - Optional Live Auto-Step advances the simulation automatically in the background.
 * - Any click, drag, scroll, or keypress pauses the auto-director for 25s and
 *   opens an interactive TV Inspector Drawer for residents, venues, or town tabs.
 */
"use strict";

class TVDirector {
  constructor() {
    this.active = false;
    this.pausedByUser = false;
    this.idleSecondsLeft = 0;
    this.liveStepSeconds = 0; // 0 = off, 6/10/15s = auto-advance simulation
    this.cameraIndex = 0;
    this.sceneElapsed = 0;
    this.sceneDuration = 12; // seconds per camera angle
    this.tickerIndex = 0;
    this.lastData = null;

    this._pollTimer = null;
    this._tickTimer = null;
    this._stepTimer = null;

    this._bindHooks();
  }

  _bindHooks() {
    window.mvUserInteracted = (reason) => {
      if (!this.active) return;
      this.pauseForInteraction(reason);
    };

    window.mvInspectVenue = async (placeId) => {
      if (!this.active) {
        document.querySelector('#tabs button[data-tab="venues"]')?.click();
        return;
      }
      this.pauseForInteraction("venue");
      await this.openVenueInspector(placeId);
    };

    // Intercept mvPick when in TV Mode so clicking a dot opens the overlay drawer
    // right on the TV map instead of navigating away from the map canvas.
    const origPick = window.mvPick;
    window.mvPick = async (agent) => {
      if (this.active) {
        this.pauseForInteraction("resident");
        await this.openResidentInspector(agent.id || agent);
      } else if (origPick) {
        origPick(agent);
      }
    };

    window.addEventListener("keydown", e => {
      if (e.target && ["INPUT", "SELECT", "TEXTAREA"].includes(e.target.tagName)) return;
      if (e.key === "t" || e.key === "T") {
        e.preventDefault();
        this.toggle();
      } else if (this.active && e.key === "Escape") {
        this.closeInspector();
        this.resumeDirector();
      } else if (this.active && e.key === " ") {
        e.preventDefault();
        if (this.pausedByUser) this.resumeDirector();
        else this.pauseForInteraction("spacebar");
      }
    });

    // Auto-launch TV mode if URL has ?tv=1
    const params = new URLSearchParams(window.location.search);
    if (params.get("tv") === "1" || params.get("mode") === "tv") {
      setTimeout(() => this.enable(), 250);
    }
    if (params.get("step")) {
      const s = Number(params.get("step"));
      if (s >= 3) this.setLiveStep(s);
    }
  }

  toggle() {
    if (this.active) this.disable();
    else this.enable();
  }

  async enable() {
    this.active = true;
    document.body.classList.add("tv-mode");
    const btn = document.getElementById("tv-toggle-btn");
    if (btn) {
      btn.classList.add("on");
      btn.textContent = "📺 Exit TV Mode";
    }
    const hud = document.getElementById("tv-hud");
    if (hud) hud.hidden = false;

    // Ensure Map panel is active and instantiated
    const mapTabBtn = document.querySelector('#tabs button[data-tab="map"]');
    if (mapTabBtn && !mapTabBtn.classList.contains("on")) {
      mapTabBtn.click();
    } else if (window.renderMap) {
      await window.renderMap(document.getElementById("map-host"));
    }

    if (window._map) window._map.tvMode = true;
    this.pausedByUser = false;
    this.sceneElapsed = 0;

    await this.refresh();
    this._applyCameraScene(true);

    clearInterval(this._pollTimer);
    clearInterval(this._tickTimer);
    this._pollTimer = setInterval(() => this.refresh(), 4000);
    this._tickTimer = setInterval(() => this._secondTick(), 1000);
  }

  disable() {
    this.active = false;
    document.body.classList.remove("tv-mode");
    const btn = document.getElementById("tv-toggle-btn");
    if (btn) {
      btn.classList.remove("on");
      btn.textContent = "📺 TV Mode";
    }
    const hud = document.getElementById("tv-hud");
    if (hud) hud.hidden = true;
    this.closeInspector();
    if (window._map) {
      window._map.tvMode = false;
      window._map.spotlightTarget = null;
      window._map.resetView();
      window._map.refresh();
    }
    clearInterval(this._pollTimer);
    clearInterval(this._tickTimer);
  }

  setLiveStep(seconds) {
    this.liveStepSeconds = Number(seconds) || 0;
    clearInterval(this._stepTimer);
    const sel = document.getElementById("tv-step-select");
    if (sel && String(sel.value) !== String(this.liveStepSeconds)) {
      sel.value = String(this.liveStepSeconds);
    }
    if (this.liveStepSeconds > 0) {
      this._stepTimer = setInterval(async () => {
        if (!this.active) return;
        await this.stepNow(1);
      }, this.liveStepSeconds * 1000);
    }
  }

  async stepNow(ticks = 1) {
    try {
      const res = await fetch(`/api/tv/step?ticks=${ticks}`, { method: "POST" });
      if (res.ok) {
        const data = await res.json();
        this._renderSnapshot(data);
        if (window._map) await window._map.refresh();
      }
    } catch (err) {
      console.warn("TV step failed:", err);
    }
  }

  pauseForInteraction(reason = "input") {
    this.pausedByUser = true;
    this.idleSecondsLeft = 25;
    this._updateDirectorStatusUI();
  }

  resumeDirector() {
    this.pausedByUser = false;
    this.idleSecondsLeft = 0;
    this.sceneElapsed = 0;
    this.closeInspector();
    this._updateDirectorStatusUI();
    this._applyCameraScene(true);
  }

  nextCameraScene() {
    this.pausedByUser = false;
    this.sceneElapsed = 0;
    const targets = this.lastData?.camera_targets || [];
    if (targets.length) {
      this.cameraIndex = (this.cameraIndex + 1) % targets.length;
    }
    this._applyCameraScene(true);
    this._updateDirectorStatusUI();
  }

  _secondTick() {
    if (!this.active) return;
    if (this.pausedByUser) {
      this.idleSecondsLeft = Math.max(0, this.idleSecondsLeft - 1);
      this._updateDirectorStatusUI();
      if (this.idleSecondsLeft === 0) {
        this.resumeDirector();
      }
      return;
    }

    this.sceneElapsed += 1;
    const pct = Math.min(100, Math.round((this.sceneElapsed / this.sceneDuration) * 100));
    const bar = document.getElementById("tv-camera-prog");
    if (bar) bar.style.width = `${pct}%`;

    if (this.sceneElapsed >= this.sceneDuration) {
      this.sceneElapsed = 0;
      const targets = this.lastData?.camera_targets || [];
      if (targets.length) {
        this.cameraIndex = (this.cameraIndex + 1) % targets.length;
      }
      this._applyCameraScene(true);
    }

    // Rotate lower-third ticker highlight every 6 seconds
    if (this.sceneElapsed % 6 === 0 && this.lastData?.ticker?.length) {
      this.tickerIndex = (this.tickerIndex + 1) % this.lastData.ticker.length;
      this._renderTickerHighlight();
    }
  }

  _updateDirectorStatusUI() {
    const badge = document.getElementById("tv-director-state");
    const resumeBtn = document.getElementById("tv-resume-btn");
    if (!badge) return;
    if (this.pausedByUser) {
      badge.className = "tv-badge interactive";
      badge.textContent = `🕹️ Interactive Inspect · Auto-Director resumes in ${this.idleSecondsLeft}s`;
      if (resumeBtn) resumeBtn.hidden = false;
    } else {
      const targets = this.lastData?.camera_targets || [];
      const cur = targets[this.cameraIndex % Math.max(1, targets.length)];
      badge.className = "tv-badge auto";
      badge.textContent = `🎬 Auto-Director · ${cur ? cur.title : "Scanning Town"}`;
      if (resumeBtn) resumeBtn.hidden = true;
    }
  }

  _applyCameraScene(forcePan = false) {
    if (!this.lastData || !window._map) return;
    const targets = this.lastData.camera_targets || [];
    if (!targets.length) return;
    const cur = targets[this.cameraIndex % targets.length];
    if (!this.pausedByUser && forcePan) {
      window._map.focusOn(cur.x, cur.y, cur.scale);
    }
    if (cur.mode === "resident" && this.lastData.spotlight_resident) {
      window._map.spotlightTarget = {
        x: this.lastData.spotlight_resident.x,
        y: this.lastData.spotlight_resident.y,
        radius: 24,
      };
    } else if (cur.mode === "venue" && this.lastData.spotlight_venue) {
      window._map.spotlightTarget = {
        x: this.lastData.spotlight_venue.x,
        y: this.lastData.spotlight_venue.y,
        radius: 34,
      };
    } else {
      window._map.spotlightTarget = null;
    }
    this._updateDirectorStatusUI();
  }

  async refresh() {
    if (!this.active) return;
    try {
      const data = await api("/api/tv");
      const tickChanged = !this.lastData || this.lastData.tick !== data.tick;
      this._renderSnapshot(data);
      if (window._map && tickChanged) {
        await window._map.refresh();
      }
    } catch (err) {
      console.warn("TV refresh error:", err);
    }
  }

  _renderSnapshot(data) {
    this.lastData = data;
    if (window._map) {
      window._map.setDirectorState(data);
      if (!this.pausedByUser) this._applyCameraScene(false);
    }

    // 1. Top-Left Clock & Town Vitality Bar
    const clockEl = document.getElementById("tv-clock");
    if (clockEl) {
      const hol = data.holiday ? ` · 🎉 ${data.holiday}` : "";
      clockEl.innerHTML = `
        <div class="tv-date">${data.date} · <span class="tv-time">${data.time}</span></div>
        <div class="tv-sky">${data.sky?.label || data.season}${hol} · Day ${data.day}</div>
      `;
    }

    const vit = data.vitality || {};
    const vitEl = document.getElementById("tv-vitality");
    if (vitEl) {
      vitEl.innerHTML = `
        <span>👥 <b>${vit.population ?? 0}</b> residents (${vit.awake ?? 0} awake)</span>
        <span>💼 <b>${vit.employed ?? 0}</b> employed</span>
        <span>🏛️ <b>$${(vit.treasury_dollars ?? 0).toLocaleString()}</b> treasury</span>
        <span>🚶 <b>${vit.active_movers ?? 0}</b> featured movers</span>
      `;
    }

    // 2. Right-Hand Spotlight Resident Card
    const sr = data.spotlight_resident;
    const srEl = document.getElementById("tv-spot-resident");
    if (srEl && sr) {
      const ambHtml = sr.ambition
        ? `<div class="tv-amb">
             <div class="tv-amb-label">🎯 ${sr.ambition.label} <b>${sr.ambition.progress_pct}%</b></div>
             <div class="prog-bar"><div class="prog-fill" style="width:${sr.ambition.progress_pct}%"></div></div>
           </div>`
        : "";
      const petHtml = sr.pet ? `<div class="tv-meta-line">🐾 Companion: <b>${sr.pet}</b></div>` : "";
      const tradHtml = sr.tradition ? `<div class="tv-meta-line">🏡 Tradition: <i>${sr.tradition}</i></div>` : "";
      const storyHtml = sr.latest_story ? `<div class="tv-story">“${sr.latest_story}”</div>` : "";

      srEl.innerHTML = `
        <div class="tv-card-head">
          <span class="tv-card-kicker">SPOTLIGHT RESIDENT · CLICK TO INSPECT</span>
          <button class="tv-card-toggle" title="Collapse / expand">−</button>
        </div>
        <div class="tv-res-head">
          <div>
            <div class="tv-res-name">${sr.name} <span class="tv-age">(${sr.age})</span></div>
            <div class="tv-res-sub">${sr.archetype} · ${sr.occupation}</div>
          </div>
          <span class="tv-act-pill">${sr.activity}</span>
        </div>
        <div class="tv-meta-line">📍 At <b>${sr.place_name}</b> · Mood: <b class="mood">${sr.mood}</b> · Wallet: <b>$${sr.money_dollars.toLocaleString()}</b></div>
        <div class="tv-meta-line">🛠️ Knack: <b>${sr.knack}</b> · Standing: <b>${sr.standing >= 0 ? "+" + sr.standing : sr.standing}</b></div>
        ${petHtml}
        ${tradHtml}
        ${ambHtml}
        ${storyHtml}
      `;
      this._wireCardToggle(srEl);
      srEl.onclick = () => {
        this.pauseForInteraction("spotlight-resident-card");
        this.openResidentInspector(sr.id);
      };
    }

    // 3. Right-Hand Spotlight Venue + Town Progression Card
    const sv = data.spotlight_venue;
    const svEl = document.getElementById("tv-spot-venue");
    if (svEl && sv) {
      const occs = (sv.occupants || []).slice(0, 5)
        .map(o => `<span class="tv-occ-chip" data-aid="${o.id}">${o.name} <i>(${o.activity})</i></span>`)
        .join(" ");
      const progHtml = (data.progression || [])
        .map(p => `<div class="tv-prog-item"><small>${p.category}</small><b>${p.title}</b><span>${p.detail}</span></div>`)
        .join("");

      svEl.innerHTML = `
        <div class="tv-card-head">
          <span class="tv-card-kicker">ACTIVE VENUE &amp; VILLAGE PROGRESSION</span>
          <button class="tv-card-toggle" title="Collapse / expand">−</button>
        </div>
        <div class="tv-ven-head" data-pid="${sv.id}">
          <b>🏛️ ${sv.name}</b>
          <span class="tv-ven-count">${sv.inside} inside · ${sv.district}</span>
        </div>
        <div class="tv-occ-list">${occs || "<i class='muted'>Quiet right now</i>"}</div>
        <div class="tv-prog-grid">${progHtml}</div>
      `;
      this._wireCardToggle(svEl);
      svEl.querySelector(".tv-ven-head")?.addEventListener("click", () => {
        this.pauseForInteraction("spotlight-venue-card");
        this.openVenueInspector(sv.id);
      });
      svEl.querySelectorAll(".tv-occ-chip").forEach(el => {
        el.addEventListener("click", e => {
          e.stopPropagation();
          this.pauseForInteraction("occupant-chip");
          this.openResidentInspector(Number(el.dataset.aid));
        });
      });
    }

    // 4. Lower-Third Broadcast Ticker
    this._renderTickerHighlight();
  }

  _wireCardToggle(cardEl) {
    const btn = cardEl.querySelector(".tv-card-toggle");
    if (!btn) return;
    // A card that was collapsed stays collapsed across 4s refreshes; keep the
    // button glyph in sync with the card's state.
    btn.textContent = cardEl.classList.contains("collapsed") ? "+" : "−";
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      cardEl.classList.toggle("collapsed");
      btn.textContent = cardEl.classList.contains("collapsed") ? "+" : "−";
    });
  }

  _renderTickerHighlight() {
    const ticker = this.lastData?.ticker || [];
    const host = document.getElementById("tv-ticker-strip");
    if (!host || !ticker.length) return;

    host.innerHTML = ticker.slice(0, 6).map((item, idx) => {
      const activeCls = (idx === (this.tickerIndex % Math.min(6, ticker.length))) ? "tv-tick-item active" : "tv-tick-item";
      return `
        <div class="${activeCls}" data-aid="${item.agent_id || ""}" data-pid="${item.place_id || ""}">
          <span class="tv-tick-badge">${item.badge}</span>
          <span class="tv-tick-text">${item.text}</span>
          <span class="tv-tick-time">${item.time}</span>
        </div>
      `;
    }).join("");

    host.querySelectorAll(".tv-tick-item").forEach(el => {
      el.addEventListener("click", () => {
        const aid = Number(el.dataset.aid);
        const pid = Number(el.dataset.pid);
        this.pauseForInteraction("ticker-click");
        if (aid) this.openResidentInspector(aid);
        else if (pid) this.openVenueInspector(pid);
      });
    });
  }

  async openResidentInspector(agentId) {
    const drawer = document.getElementById("tv-inspector");
    const body = document.getElementById("tv-inspector-body");
    if (!drawer || !body) return;
    drawer.hidden = false;
    body.innerHTML = `<div class="muted">Loading resident #${agentId}…</div>`;

    try {
      const d = await api(`/api/resident/${agentId}`);
      const a = d.agent;
      const st = d.state || {};
      const ind = d.individual || {};
      const amb = d.ambition;
      const men = d.mentorship || {};

      // Center map camera smoothly on the clicked resident's venue if known
      if (window._map && st.place_id && window._map.venues[st.place_id]) {
        const v = window._map.venues[st.place_id];
        window._map.focusOn(v.x, v.y, 1.75);
        window._map.spotlightTarget = { x: v.x, y: v.y, radius: 26 };
      }

      const axesHtml = ind.axes
        ? Object.entries(ind.axes)
            .map(([k, v]) => `<span class="axis-pill">${k}: ${v >= 0 ? "+" + v : v}</span>`)
            .join(" ")
        : "";
      const ambHtml = amb
        ? `<div class="ind-box"><b>🎯 Active Ambition:</b> ${amb.label} (${Math.round((amb.progress || 0) * 100)}%)</div>`
        : "";
      const relsHtml = (d.relationships || []).slice(0, 6)
        .map(r => {
          const otherId = r.a_id === a.id ? r.b_id : r.a_id;
          return `<li class="click" data-other="${otherId}"><b>${r.label}</b> ${r.other_name} <span class="t">aff ${Math.round(r.affinity)} · fam ${Math.round(r.familiarity)}</span></li>`;
        })
        .join("");
      const memsHtml = (d.memories || []).slice(0, 6)
        .map(m => `<li class="mem ${m.kind === 'reflection' ? 'refl' : ''}">${m.text}</li>`)
        .join("");

      body.innerHTML = `
        <div class="tv-insp-header">
          <h3>👤 ${a.name} (${a.age}, ${(a.occupation || "resident").replace(/_/g, " ")})</h3>
          <p class="persona">${a.persona || ""}</p>
        </div>
        <div class="ind-box">
          <div><b>Archetype:</b> ${ind.archetype || "resident"} · <b>Chronotype:</b> ${ind.chronotype || "day_walker"} · <b>Knack:</b> ${ind.knack || "—"}</div>
          <div><b>Now:</b> ${st.activity || "idle"} (${st.mood || "ok"}) · Wallet $${Math.round((st.money_cents || 0) / 100).toLocaleString()}</div>
          ${men.household_tradition ? `<div><b>Family Tradition:</b> ${men.household_tradition}</div>` : ""}
          <div class="axes-row">${axesHtml}</div>
        </div>
        ${ambHtml}
        <div style="margin:.6rem 0">
          <button class="mini" id="tv-open-bonds-btn">🕸️ View Social Network Wheel</button>
        </div>
        <h4>Closest Bonds (click to jump)</h4>
        <ul>${relsHtml || "<li class='muted'>No close bonds yet</li>"}</ul>
        <h4>Recent Memories</h4>
        <ul>${memsHtml || "<li class='muted'>No memories yet</li>"}</ul>
      `;

      body.querySelectorAll("li[data-other]").forEach(li => {
        li.addEventListener("click", () => {
          this.pauseForInteraction("bond-jump");
          this.openResidentInspector(Number(li.dataset.other));
        });
      });

      body.querySelector("#tv-open-bonds-btn")?.addEventListener("click", () => {
        this.disable();
        if (window.mvOpenWheel) window.mvOpenWheel(a.id);
      });
    } catch (err) {
      body.innerHTML = `<div class="muted">Could not load resident details.</div>`;
    }
  }

  async openVenueInspector(placeId) {
    const drawer = document.getElementById("tv-inspector");
    const body = document.getElementById("tv-inspector-body");
    if (!drawer || !body) return;
    drawer.hidden = false;
    body.innerHTML = `<div class="muted">Loading venue #${placeId}…</div>`;

    try {
      const venues = await api("/api/venues");
      const v = venues.find(x => Number(x.id) === Number(placeId));
      if (!v) {
        body.innerHTML = `<div class="muted">Venue not found.</div>`;
        return;
      }

      if (window._map && window._map.venues[placeId]) {
        const mv = window._map.venues[placeId];
        window._map.focusOn(mv.x, mv.y, 1.55);
        window._map.spotlightTarget = { x: mv.x, y: mv.y, radius: 34 };
      }

      const occList = (v.occupants || [])
        .map(o => `<li class="click" data-aid="${o.id}"><b>${o.activity}</b> ${o.name} <span class="t">mood: ${o.mood}</span></li>`)
        .join("");

      body.innerHTML = `
        <div class="tv-insp-header">
          <h3>🏛️ ${v.name}</h3>
          <div class="muted">${v.kind.toUpperCase()} · ${v.district} · Capacity ${v.capacity}</div>
        </div>
        <div class="ind-box">
          <b>${(v.occupants || []).length} residents currently inside</b>
        </div>
        <h4>Current Occupants (click to inspect resident)</h4>
        <ul>${occList || "<li class='muted'>Nobody inside right now.</li>"}</ul>
      `;

      body.querySelectorAll("li[data-aid]").forEach(li => {
        li.addEventListener("click", () => {
          this.pauseForInteraction("venue-occupant-click");
          this.openResidentInspector(Number(li.dataset.aid));
        });
      });
    } catch (err) {
      body.innerHTML = `<div class="muted">Could not load venue details.</div>`;
    }
  }

  closeInspector() {
    const drawer = document.getElementById("tv-inspector");
    if (drawer) drawer.hidden = true;
  }
}

window.addEventListener("DOMContentLoaded", () => {
  window.mvTV = new TVDirector();
});

/* Miniville 10-Foot Living TV & Story Director (v0.44-v0.48)
 *
 * Meant to play passively on a screen while the town runs, and to let the
 * viewer grab the camera whenever something catches their eye.
 *
 * The director is a small state machine:
 *   auto      cycles the backend's camera playlist: the day's best stories,
 *             a featured resident from a current storyline, a busy venue and
 *             the panorama. A new day opens with a "previously" recap.
 *   breaking  a newsworthy event the viewer has not seen cuts in for a few
 *             seconds (a wedding, a death, a fire, a shop opening), then the
 *             director goes back to what it was doing.
 *   focus     the viewer follows a resident or a venue: the camera tracks
 *             them tick after tick and a card shows where they are, where they
 *             go next and what happened to them today. It lasts as long as the
 *             chosen focus length (or until "Back to Auto"); news arriving
 *             meanwhile shows as a toast instead of stealing the camera.
 *   inspect   any click or drag pauses the director for a short while so the
 *             viewer can look around; it resumes on its own.
 *
 * Keys: T toggle TV · F follow what's on screen · A/Esc back to auto ·
 *       N next angle · R yesterday's recap · Space pause/resume.
 */
"use strict";

const TV_PREFS_KEY = "mv.tv.prefs";
const TV_INSPECT_SECONDS = 25;
const TV_BREAKING_SECONDS = 10;
const TV_BREAKING_GAP = 20;       // seconds between two breaking cuts
const TV_RECAP_SECONDS = 14;

function tvEsc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

class TVDirector {
  constructor() {
    this.active = false;
    this.mode = "auto";               // auto | breaking | focus | inspect
    this.prevMode = "auto";           // what breaking returns to
    this.focus = null;                // {type, id, name, secondsLeft|null}
    this.inspectLeft = 0;
    this.breakingLeft = 0;
    this.breakingScene = null;
    this.breakingQueue = [];
    this.lastBreakingAt = -1e9;
    this.shownEvents = new Set();
    this.cursor = 0;                  // newest event id the viewer has seen
    this.recapLeft = 0;
    this.lastDay = null;

    const prefs = this._loadPrefs();
    this.liveStepSeconds = prefs.step ?? 0;
    this.sceneDuration = prefs.pace ?? 12;
    this.focusMinutes = prefs.focus ?? 5;   // 0 = until I come back

    this.cameraIndex = 0;
    this.sceneElapsed = 0;
    this.tickerIndex = 0;
    this.lastData = null;
    this.clock = 0;

    this._pollTimer = null;
    this._tickTimer = null;
    this._stepTimer = null;

    this._bindHooks();
  }

  // --- preferences ------------------------------------------------------------

  _loadPrefs() {
    try {
      return JSON.parse(localStorage.getItem(TV_PREFS_KEY) || "{}") || {};
    } catch (e) {
      return {};
    }
  }

  _savePrefs() {
    try {
      localStorage.setItem(TV_PREFS_KEY, JSON.stringify({
        step: this.liveStepSeconds, pace: this.sceneDuration, focus: this.focusMinutes,
      }));
    } catch (e) { /* private window: preferences just don't persist */ }
  }

  _syncSelects() {
    const set = (id, v) => {
      const el = document.getElementById(id);
      if (el && String(el.value) !== String(v)) el.value = String(v);
    };
    set("tv-step-select", this.liveStepSeconds);
    set("tv-pace-select", this.sceneDuration);
    set("tv-focus-select", this.focusMinutes);
  }

  // --- wiring -----------------------------------------------------------------

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

    // In TV mode a click on a dot opens the inspector over the map instead of
    // navigating away to the resident card.
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
      const k = e.key;
      if (k === "t" || k === "T") {
        e.preventDefault();
        this.toggle();
        return;
      }
      if (!this.active) return;
      if (k === "Escape" || k === "a" || k === "A") {
        e.preventDefault();
        this.backToAuto();
      } else if (k === "f" || k === "F") {
        e.preventDefault();
        this.followOnScreen();
      } else if (k === "n" || k === "N") {
        e.preventDefault();
        this.nextCameraScene();
      } else if (k === "r" || k === "R") {
        e.preventDefault();
        this.showRecap();
      } else if (k === " ") {
        e.preventDefault();
        if (this.mode === "inspect") this.backToAuto();
        else this.pauseForInteraction("spacebar");
      }
    });

    const params = new URLSearchParams(window.location.search);
    if (params.get("step")) {
      const s = Number(params.get("step"));
      if (s >= 3) this.liveStepSeconds = s;
    }
    if (params.get("tv") === "1" || params.get("mode") === "tv") {
      setTimeout(() => this.enable(), 250);
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
    this._syncSelects();

    const mapTabBtn = document.querySelector('#tabs button[data-tab="map"]');
    if (mapTabBtn && !mapTabBtn.classList.contains("on")) {
      mapTabBtn.click();
    } else if (window.renderMap) {
      await window.renderMap(document.getElementById("map-host"));
    }

    if (window._map) window._map.tvMode = true;
    this.mode = "auto";
    this.focus = null;
    this.sceneElapsed = 0;

    await this.refresh();
    this._applyCameraScene(true);
    this.setLiveStep(this.liveStepSeconds);

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
    this._hideBreaking();
    this._hideRecap();
    if (window._map) {
      window._map.tvMode = false;
      window._map.spotlightTarget = null;
      window._map.hotspots = [];
      window._map.resetView();
      window._map.refresh();
    }
    clearInterval(this._pollTimer);
    clearInterval(this._tickTimer);
    clearInterval(this._stepTimer);
  }

  // --- settings ---------------------------------------------------------------

  setLiveStep(seconds) {
    this.liveStepSeconds = Number(seconds) || 0;
    clearInterval(this._stepTimer);
    this._syncSelects();
    this._savePrefs();
    if (this.liveStepSeconds > 0) {
      this._stepTimer = setInterval(async () => {
        if (!this.active) return;
        await this.stepNow(1);
      }, this.liveStepSeconds * 1000);
    }
  }

  setPace(seconds) {
    this.sceneDuration = Math.max(6, Number(seconds) || 12);
    this._savePrefs();
  }

  setFocusLength(minutes) {
    this.focusMinutes = Number(minutes) || 0;
    if (this.focus) this.focus.secondsLeft = this.focusMinutes ? this.focusMinutes * 60 : null;
    this._savePrefs();
    this._renderFocusCard();
  }

  async stepNow(ticks = 1) {
    try {
      const res = await fetch(`/api/tv/step?ticks=${ticks}`, { method: "POST" });
      if (res.ok) {
        await this.refresh(true);
      }
    } catch (err) {
      console.warn("TV step failed:", err);
    }
  }

  // --- director state ---------------------------------------------------------

  pauseForInteraction(reason = "input") {
    if (this.mode === "focus") return;          // following already owns the camera
    if (this.mode === "breaking") this._endBreaking(false);
    this.mode = "inspect";
    this.inspectLeft = TV_INSPECT_SECONDS;
    this._hideRecap();
    this._updateDirectorStatusUI();
  }

  // kept for the old HTML hooks
  resumeDirector() {
    this.backToAuto();
  }

  backToAuto() {
    this.mode = "auto";
    this.focus = null;
    this.inspectLeft = 0;
    this.sceneElapsed = 0;
    this.closeInspector();
    this._hideBreaking();
    this._renderFocusCard();
    this._updateDirectorStatusUI();
    this._applyCameraScene(true);
  }

  nextCameraScene() {
    if (this.mode !== "auto") this.backToAuto();
    this.sceneElapsed = 0;
    this.recapLeft = 0;
    this._hideRecap();
    const targets = this.lastData?.camera_targets || [];
    if (targets.length) this.cameraIndex = (this.cameraIndex + 1) % targets.length;
    this._applyCameraScene(true);
    this._updateDirectorStatusUI();
  }

  async follow(type, id, name = "") {
    if (!id) return;
    this.mode = "focus";
    this.focus = {
      type, id: Number(id), name,
      secondsLeft: this.focusMinutes ? this.focusMinutes * 60 : null,
    };
    this._hideBreaking();
    this._hideRecap();
    this.closeInspector();
    this._renderFocusCard();
    this._updateDirectorStatusUI();
    await this.refresh(true);
  }

  /** F: follow whoever or whatever the camera is showing right now. */
  followOnScreen() {
    if (this.mode === "breaking" && this.breakingScene) {
      const sc = this.breakingScene;
      if (sc.agent_id) return this.follow("resident", sc.agent_id);
      if (sc.place_id) return this.follow("venue", sc.place_id);
    }
    const cur = this.scene;
    if (cur?.agent_id) return this.follow("resident", cur.agent_id);
    if (cur?.place_id) return this.follow("venue", cur.place_id);
    const sr = this.lastData?.spotlight_resident;
    if (sr) return this.follow("resident", sr.id, sr.name);
  }

  _secondTick() {
    if (!this.active) return;
    this.clock += 1;

    if (this.mode === "inspect") {
      this.inspectLeft = Math.max(0, this.inspectLeft - 1);
      if (this.inspectLeft === 0) this.backToAuto();
      else this._updateDirectorStatusUI();
      this._offerQueuedNews();
      return;
    }

    if (this.mode === "focus") {
      if (this.focus && this.focus.secondsLeft != null) {
        this.focus.secondsLeft -= 1;
        if (this.focus.secondsLeft <= 0) {
          this.backToAuto();
          return;
        }
      }
      this._renderFocusCard(true);
      this._updateDirectorStatusUI();
      this._offerQueuedNews();
      return;
    }

    if (this.mode === "breaking") {
      this.breakingLeft -= 1;
      this._setProgress(100 - (this.breakingLeft / TV_BREAKING_SECONDS) * 100);
      if (this.breakingLeft <= 0) this._endBreaking(true);
      return;
    }

    // auto: a recap plays out (it is short), then queued news cuts in
    if (this.recapLeft <= 0 && this.breakingQueue.length
        && this.clock - this.lastBreakingAt >= TV_BREAKING_GAP) {
      this._playBreaking(this.breakingQueue.shift());
      return;
    }
    if (this.recapLeft > 0) {
      this.recapLeft -= 1;
      this._setProgress(100 - (this.recapLeft / TV_RECAP_SECONDS) * 100);
      if (this.recapLeft === 0) {
        this._hideRecap();
        this._applyCameraScene(true);
      }
      return;
    }

    this.sceneElapsed += 1;
    this._setProgress((this.sceneElapsed / this.sceneDuration) * 100);
    if (this.sceneElapsed >= this.sceneDuration) {
      this.sceneElapsed = 0;
      const targets = this.lastData?.camera_targets || [];
      if (targets.length) this.cameraIndex = (this.cameraIndex + 1) % targets.length;
      this._applyCameraScene(true);
    }
    if (this.sceneElapsed % 6 === 0 && this.lastData?.ticker?.length) {
      this.tickerIndex = (this.tickerIndex + 1) % this.lastData.ticker.length;
      this._renderTickerHighlight();
    }
  }

  _setProgress(pct) {
    const bar = document.getElementById("tv-camera-prog");
    if (bar) bar.style.width = `${Math.max(0, Math.min(100, Math.round(pct)))}%`;
  }

  _updateDirectorStatusUI() {
    const badge = document.getElementById("tv-director-state");
    const resumeBtn = document.getElementById("tv-resume-btn");
    if (!badge) return;
    const showResume = this.mode === "inspect" || this.mode === "focus";
    if (resumeBtn) resumeBtn.hidden = !showResume;
    if (this.mode === "inspect") {
      badge.className = "tv-badge interactive";
      badge.textContent = `🕹️ Looking around · auto in ${this.inspectLeft}s`;
    } else if (this.mode === "focus" && this.focus) {
      badge.className = "tv-badge focus";
      const left = this.focus.secondsLeft == null
        ? "until you return"
        : `${Math.floor(this.focus.secondsLeft / 60)}:${String(this.focus.secondsLeft % 60).padStart(2, "0")}`;
      badge.textContent = `📌 Following ${this.focus.name || "#" + this.focus.id} · ${left}`;
    } else if (this.mode === "breaking") {
      badge.className = "tv-badge breaking";
      badge.textContent = "🔴 Breaking news";
    } else if (this.recapLeft > 0) {
      badge.className = "tv-badge auto";
      badge.textContent = "🎬 Auto-Director · Previously in Miniville";
    } else {
      const cur = this.scene;
      badge.className = "tv-badge auto";
      const title = cur ? (cur.mode === "story" ? cur.category : cur.title) : "Scanning town";
      badge.textContent = `🎬 Auto-Director · ${title}`;
    }
  }

  /** The scene on air. A scene keeps its subject for its whole duration: the
   * backend re-ranks the busiest venues and the storyline cast on every
   * refresh, and taking its newest pick each time swapped the featured
   * resident mid-shot. Between cuts only the subject's position updates. */
  _currentScene(cut) {
    const targets = this.lastData?.camera_targets || [];
    if (!targets.length) return null;
    if (cut || !this.scene) {
      this.scene = { ...targets[this.cameraIndex % targets.length] };
      return this.scene;
    }
    const same = targets.find(t => t.mode === this.scene.mode && (
      (t.mode === "story" && t.event_id === this.scene.event_id) ||
      (t.mode === "resident" && t.agent_id === this.scene.agent_id) ||
      (t.mode === "venue" && t.place_id === this.scene.place_id) ||
      t.mode === "overview"));
    if (same) Object.assign(this.scene, { x: same.x, y: same.y, subtitle: same.subtitle });
    return this.scene;
  }

  _applyCameraScene(forcePan = false) {
    if (!this.lastData || !window._map) return;
    if (this.mode === "focus") return this._trackFocus(forcePan);
    if (this.mode !== "auto") return;
    const cur = this._currentScene(forcePan);
    if (!cur) return;
    if (forcePan) window._map.focusOn(cur.x, cur.y, cur.scale);
    if (cur.mode === "story") {
      window._map.spotlightTarget = { x: cur.x, y: cur.y, radius: 28 };
      this._showStoryCaption(cur);
    } else {
      this._showStoryCaption(null);
      if (cur.mode === "resident") {
        window._map.spotlightTarget = { x: cur.x, y: cur.y, radius: 24 };
      } else if (cur.mode === "venue") {
        window._map.spotlightTarget = { x: cur.x, y: cur.y, radius: 34 };
      } else {
        window._map.spotlightTarget = null;
      }
    }
    this._updateDirectorStatusUI();
  }

  _trackFocus(forcePan) {
    const f = this.focus;
    const d = f && (f.type === "resident" ? this.lastData.followed : this.lastData.followed_venue);
    if (!d || !window._map) return;
    const moved = !this._focusXY || this._focusXY.x !== d.x || this._focusXY.y !== d.y;
    if (forcePan || moved) {
      window._map.focusOn(d.x, d.y, f.type === "resident" ? 1.9 : 1.6);
      this._focusXY = { x: d.x, y: d.y };
    }
    window._map.spotlightTarget = { x: d.x, y: d.y, radius: f.type === "resident" ? 22 : 34 };
    this._showStoryCaption(null);
  }

  // --- breaking news, recap ----------------------------------------------------

  _queueBreaking(scenes) {
    for (const sc of scenes || []) {
      if (this.shownEvents.has(sc.event_id)) continue;
      this.shownEvents.add(sc.event_id);
      this.breakingQueue.push(sc);
    }
    // never let a backlog build: the freshest few are the news
    this.breakingQueue.sort((a, b) => b.score - a.score);
    this.breakingQueue = this.breakingQueue.slice(0, 3);
  }

  _offerQueuedNews() {
    // while the viewer is busy, news arrives as a toast with a "Watch" button
    while (this.breakingQueue.length) {
      this._toast(this.breakingQueue.shift());
    }
  }

  _playBreaking(sc) {
    this.prevMode = "auto";
    this.mode = "breaking";
    this.breakingScene = sc;
    this.breakingLeft = TV_BREAKING_SECONDS;
    this.lastBreakingAt = this.clock;
    this._hideRecap();
    this._showStoryCaption(null);
    if (window._map) {
      window._map.focusOn(sc.x, sc.y, 1.9);
      window._map.spotlightTarget = { x: sc.x, y: sc.y, radius: 30 };
      window._map.flash = { x: sc.x, y: sc.y, t: 0 };
    }
    const el = document.getElementById("tv-breaking");
    if (el) {
      el.innerHTML = `
        <div class="tv-brk-kicker">🔴 BREAKING · ${tvEsc(sc.category).toUpperCase()}</div>
        <div class="tv-brk-headline">${tvEsc(sc.headline)}</div>
        <div class="tv-brk-sub">${tvEsc(sc.subtitle)}</div>
        <div class="tv-brk-actions">
          ${sc.agent_id ? `<button class="tv-btn accent" data-follow="resident" data-id="${sc.agent_id}">📌 Follow this story</button>` : ""}
          ${!sc.agent_id && sc.place_id ? `<button class="tv-btn accent" data-follow="venue" data-id="${sc.place_id}">📌 Watch this place</button>` : ""}
        </div>`;
      el.hidden = false;
      el.querySelectorAll("[data-follow]").forEach(b => b.addEventListener("click", ev => {
        ev.stopPropagation();
        this.follow(b.dataset.follow, Number(b.dataset.id));
      }));
    }
    this._updateDirectorStatusUI();
  }

  _endBreaking(returnToScene) {
    this._hideBreaking();
    this.breakingScene = null;
    this.mode = this.prevMode || "auto";
    this.sceneElapsed = 0;
    if (returnToScene) this._applyCameraScene(true);
    this._updateDirectorStatusUI();
  }

  _hideBreaking() {
    const el = document.getElementById("tv-breaking");
    if (el) el.hidden = true;
    if (this.mode === "breaking") this.mode = "auto";
  }

  _toast(sc) {
    const host = document.getElementById("tv-toasts");
    if (!host) return;
    const t = document.createElement("div");
    t.className = "tv-toast tv-glass";
    t.innerHTML = `
      <div class="tv-toast-kicker">🔴 ${tvEsc(sc.category)}</div>
      <div class="tv-toast-text">${tvEsc(sc.headline)}</div>
      <div class="tv-toast-actions">
        <button class="tv-btn accent" data-act="watch">▶ Watch</button>
        <button class="tv-btn" data-act="dismiss">✕</button>
      </div>`;
    t.querySelector('[data-act="watch"]').addEventListener("click", () => {
      t.remove();
      this.backToAuto();
      this._playBreaking(sc);
    });
    t.querySelector('[data-act="dismiss"]').addEventListener("click", () => t.remove());
    host.prepend(t);
    while (host.children.length > 3) host.lastChild.remove();
    setTimeout(() => t.remove(), 45000);
  }

  showRecap() {
    const rc = this.lastData?.recap;
    if (!rc || !(rc.stories || []).length) return;
    if (this.mode !== "auto") this.backToAuto();
    this.recapLeft = TV_RECAP_SECONDS;
    if (window._map) {
      window._map.resetView();
      window._map.spotlightTarget = null;
    }
    this._showStoryCaption(null);
    const el = document.getElementById("tv-recap");
    if (el) {
      el.innerHTML = `
        <div class="tv-brk-kicker">PREVIOUSLY IN MINIVILLE · ${tvEsc(rc.date)}</div>
        <ol class="tv-recap-list">${rc.stories.map(s => `
          <li data-aid="${s.agent_id || ""}" data-pid="${s.place_id || ""}">
            <span class="tv-recap-cat">${tvEsc(s.category)}</span> ${tvEsc(s.text)}
          </li>`).join("")}
        </ol>
        <div class="tv-brk-sub">${rc.events.toLocaleString()} things happened that day</div>`;
      el.hidden = false;
      el.querySelectorAll("li[data-aid]").forEach(li => li.addEventListener("click", () => {
        const aid = Number(li.dataset.aid), pid = Number(li.dataset.pid);
        if (aid) this.follow("resident", aid);
        else if (pid) this.follow("venue", pid);
      }));
    }
    this._updateDirectorStatusUI();
  }

  _hideRecap() {
    this.recapLeft = 0;
    const el = document.getElementById("tv-recap");
    if (el) el.hidden = true;
  }

  _showStoryCaption(sc) {
    const el = document.getElementById("tv-caption");
    if (!el) return;
    if (!sc) {
      el.hidden = true;
      return;
    }
    el.innerHTML = `
      <span class="tv-cap-cat">${tvEsc(sc.category)}</span>
      <span class="tv-cap-text">${tvEsc(sc.headline)}</span>
      <span class="tv-cap-sub">${tvEsc(sc.subtitle)}</span>`;
    el.hidden = false;
    el.onclick = () => {
      if (sc.agent_id) this.follow("resident", sc.agent_id);
      else if (sc.place_id) this.follow("venue", sc.place_id);
    };
  }

  // --- data ---------------------------------------------------------------------

  _snapshotUrl() {
    const q = new URLSearchParams();
    if (this.cursor) q.set("since", String(this.cursor));
    if (this.mode === "focus" && this.focus) {
      q.set(this.focus.type === "resident" ? "follow" : "follow_place", String(this.focus.id));
    }
    const s = q.toString();
    return "/api/tv" + (s ? "?" + s : "");
  }

  async refresh(forceMap = false) {
    if (!this.active) return;
    try {
      const data = await api(this._snapshotUrl());
      const tickChanged = !this.lastData || this.lastData.tick !== data.tick;
      const firstLook = !this.cursor;
      this._renderSnapshot(data);
      if (!firstLook) this._queueBreaking(data.breaking);
      this.cursor = Math.max(this.cursor, data.latest_event_id || 0);
      if (this.lastDay != null && data.day !== this.lastDay && this.mode === "auto") {
        this.showRecap();
      }
      this.lastDay = data.day;
      if (window._map && (tickChanged || forceMap)) await window._map.refresh();
    } catch (err) {
      console.warn("TV refresh error:", err);
    }
  }

  _renderSnapshot(data) {
    this.lastData = data;
    if (this.focus && this.mode === "focus") {
      const d = this.focus.type === "resident" ? data.followed : data.followed_venue;
      if (d && d.name) this.focus.name = d.name;
    }
    if (window._map) {
      window._map.setDirectorState(data);
      window._map.hotspots = data.hotspots || [];
      if (this.mode === "auto" && this.recapLeft === 0) this._applyCameraScene(false);
      if (this.mode === "focus") this._trackFocus(false);
    }

    const clockEl = document.getElementById("tv-clock");
    if (clockEl) {
      const hol = data.holiday ? ` · 🎉 ${tvEsc(data.holiday)}` : "";
      clockEl.innerHTML = `
        <div class="tv-date">${tvEsc(data.date)} · <span class="tv-time">${tvEsc(data.time)}</span></div>
        <div class="tv-sky">${tvEsc(data.sky?.label || data.season)}${hol} · Day ${data.day}</div>`;
    }

    const vit = data.vitality || {};
    const vitEl = document.getElementById("tv-vitality");
    if (vitEl) {
      vitEl.innerHTML = `
        <span>👥 <b>${vit.population ?? 0}</b> residents (${vit.awake ?? 0} awake)</span>
        <span>💼 <b>${vit.employed ?? 0}</b> employed</span>
        <span>🏛️ <b>$${(vit.treasury_dollars ?? 0).toLocaleString()}</b> treasury</span>
        <span>📰 <b>${(data.stories || []).length}</b> stories today</span>`;
    }

    this._renderSpotlightCards(data);
    this._renderFocusCard();
    this._renderTickerHighlight();
    this._updateDirectorStatusUI();
  }

  _renderSpotlightCards(data) {
    const sr = data.spotlight_resident;
    const srEl = document.getElementById("tv-spot-resident");
    if (srEl && sr) {
      const ambHtml = sr.ambition
        ? `<div class="tv-amb">
             <div class="tv-amb-label">🎯 ${tvEsc(sr.ambition.label)} <b>${sr.ambition.progress_pct}%</b></div>
             <div class="prog-bar"><div class="prog-fill" style="width:${sr.ambition.progress_pct}%"></div></div>
           </div>`
        : "";
      const petHtml = sr.pet ? `<div class="tv-meta-line">🐾 Companion: <b>${tvEsc(sr.pet)}</b></div>` : "";
      const tradHtml = sr.tradition ? `<div class="tv-meta-line">🏡 Tradition: <i>${tvEsc(sr.tradition)}</i></div>` : "";
      const storyHtml = sr.latest_story ? `<div class="tv-story">“${tvEsc(sr.latest_story)}”</div>` : "";
      srEl.innerHTML = `
        <div class="tv-card-head">
          <span class="tv-card-kicker">IN THE STORY TODAY</span>
          <span>
            <button class="tv-card-follow" title="Follow this resident (F)">📌</button>
            <button class="tv-card-toggle" title="Collapse / expand">−</button>
          </span>
        </div>
        <div class="tv-res-head">
          <div>
            <div class="tv-res-name">${tvEsc(sr.name)} <span class="tv-age">(${sr.age})</span></div>
            <div class="tv-res-sub">${tvEsc(sr.archetype)} · ${tvEsc(sr.occupation)}</div>
          </div>
          <span class="tv-act-pill">${tvEsc(sr.activity)}</span>
        </div>
        <div class="tv-meta-line">📍 At <b>${tvEsc(sr.place_name)}</b> · Mood: <b class="mood">${tvEsc(sr.mood)}</b> · Wallet: <b>$${sr.money_dollars.toLocaleString()}</b></div>
        <div class="tv-meta-line">🛠️ Knack: <b>${tvEsc(sr.knack)}</b> · Standing: <b>${sr.standing >= 0 ? "+" + sr.standing : sr.standing}</b></div>
        ${petHtml}${tradHtml}${ambHtml}${storyHtml}`;
      this._wireCardToggle(srEl);
      srEl.querySelector(".tv-card-follow")?.addEventListener("click", e => {
        e.stopPropagation();
        this.follow("resident", sr.id, sr.name);
      });
      srEl.onclick = () => {
        this.pauseForInteraction("spotlight-resident-card");
        this.openResidentInspector(sr.id);
      };
    }

    const sv = data.spotlight_venue;
    const svEl = document.getElementById("tv-spot-venue");
    if (svEl && sv) {
      const occs = (sv.occupants || []).slice(0, 5)
        .map(o => `<span class="tv-occ-chip" data-aid="${o.id}">${tvEsc(o.name)} <i>(${tvEsc(o.activity)})</i></span>`)
        .join(" ");
      const progHtml = (data.progression || [])
        .map(p => `<div class="tv-prog-item"><small>${tvEsc(p.category)}</small><b>${tvEsc(p.title)}</b><span>${tvEsc(p.detail)}</span></div>`)
        .join("");
      svEl.innerHTML = `
        <div class="tv-card-head">
          <span class="tv-card-kicker">ACTIVE VENUE &amp; VILLAGE PROGRESSION</span>
          <span>
            <button class="tv-card-follow" title="Watch this venue">📌</button>
            <button class="tv-card-toggle" title="Collapse / expand">−</button>
          </span>
        </div>
        <div class="tv-ven-head" data-pid="${sv.id}">
          <b>🏛️ ${tvEsc(sv.name)}</b>
          <span class="tv-ven-count">${sv.inside} inside · ${tvEsc(sv.district)}</span>
        </div>
        <div class="tv-occ-list">${occs || "<i class='muted'>Quiet right now</i>"}</div>
        <div class="tv-prog-grid">${progHtml}</div>`;
      this._wireCardToggle(svEl);
      svEl.querySelector(".tv-card-follow")?.addEventListener("click", e => {
        e.stopPropagation();
        this.follow("venue", sv.id, sv.name);
      });
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
  }

  /** The follow card: where the followed resident/venue is, what's next, today. */
  _renderFocusCard(timerOnly = false) {
    const el = document.getElementById("tv-focus");
    if (!el) return;
    const rail = document.querySelector(".tv-right-rail");
    if (this.mode !== "focus" || !this.focus || !this.lastData) {
      el.hidden = true;
      if (rail) rail.classList.remove("focusing");
      return;
    }
    if (rail) rail.classList.add("focusing");
    el.hidden = false;
    const f = this.focus;
    const left = f.secondsLeft == null
      ? "until you return"
      : `${Math.floor(f.secondsLeft / 60)}:${String(f.secondsLeft % 60).padStart(2, "0")} left`;
    const timer = el.querySelector(".tv-focus-timer");
    if (timerOnly && timer) {
      timer.textContent = left;
      return;
    }
    const today = (items) => (items || []).length
      ? `<ul class="tv-focus-list">${items.map(t => `<li><span class="t">${tvEsc(t.time)}</span> ${tvEsc(t.text)}</li>`).join("")}</ul>`
      : `<div class="muted">Nothing yet today.</div>`;
    let body = `<div class="muted">Finding them…</div>`;
    if (f.type === "resident" && this.lastData.followed) {
      const d = this.lastData.followed;
      const next = (d.upcoming || []).length
        ? `<ul class="tv-focus-list">${d.upcoming.map(u => `<li><span class="t">${tvEsc(u.time)}</span> ${tvEsc(u.activity.replace(/_/g, " "))} · ${tvEsc(u.place)}</li>`).join("")}</ul>`
        : `<div class="muted">Settling in for the night.</div>`;
      body = `
        <div class="tv-res-name">${tvEsc(d.name)} <span class="tv-age">(${d.age})</span></div>
        <div class="tv-res-sub">${tvEsc(d.occupation)}${d.alive ? "" : " · <b>deceased</b>"}</div>
        <div class="tv-meta-line">📍 <b>${tvEsc(d.activity || "")}</b> at <b>${tvEsc(d.place_name)}</b> · ${tvEsc(d.mood || "")}</div>
        <h5>Next</h5>${next}
        <h5>Today</h5>${today(d.today)}`;
    } else if (f.type === "venue" && this.lastData.followed_venue) {
      const d = this.lastData.followed_venue;
      const occ = (d.occupants || []).map(o =>
        `<span class="tv-occ-chip" data-aid="${o.id}">${tvEsc(o.name)} <i>(${tvEsc(o.activity)})</i></span>`).join(" ");
      body = `
        <div class="tv-res-name">🏛️ ${tvEsc(d.name)}</div>
        <div class="tv-res-sub">${tvEsc(d.district)} · ${d.inside} inside of ${d.capacity}</div>
        <div class="tv-occ-list">${occ || "<i class='muted'>Empty right now</i>"}</div>
        <h5>Today here</h5>${today(d.today)}`;
    }
    el.innerHTML = `
      <div class="tv-card-head">
        <span class="tv-card-kicker">📌 FOLLOWING · <span class="tv-focus-timer">${left}</span></span>
      </div>
      ${body}
      <div class="tv-focus-actions">
        <button class="tv-btn accent" data-act="auto">↩ Back to Auto (A)</button>
        <button class="tv-btn" data-act="inspect">🔎 Details</button>
      </div>`;
    el.querySelector('[data-act="auto"]').addEventListener("click", () => this.backToAuto());
    el.querySelector('[data-act="inspect"]').addEventListener("click", () => {
      if (f.type === "resident") this.openResidentInspector(f.id);
      else this.openVenueInspector(f.id);
    });
    el.querySelectorAll(".tv-occ-chip").forEach(c => c.addEventListener("click", () => {
      this.follow("resident", Number(c.dataset.aid));
    }));
  }

  _wireCardToggle(cardEl) {
    const btn = cardEl.querySelector(".tv-card-toggle");
    if (!btn) return;
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
          <span class="tv-tick-badge">${tvEsc(item.badge)}</span>
          <span class="tv-tick-text">${tvEsc(item.text)}</span>
          <span class="tv-tick-time">${tvEsc(item.time)}</span>
        </div>`;
    }).join("");
    host.querySelectorAll(".tv-tick-item").forEach(el => {
      el.addEventListener("click", () => {
        const aid = Number(el.dataset.aid);
        const pid = Number(el.dataset.pid);
        if (aid) this.follow("resident", aid);
        else if (pid) this.follow("venue", pid);
      });
    });
  }

  // --- inspectors ---------------------------------------------------------------

  async openResidentInspector(agentId) {
    const drawer = document.getElementById("tv-inspector");
    const body = document.getElementById("tv-inspector-body");
    if (!drawer || !body) return;
    drawer.hidden = false;
    body.innerHTML = `<div class="muted">Loading resident #${Number(agentId)}…</div>`;
    try {
      const d = await api(`/api/resident/${agentId}`);
      const a = d.agent;
      const st = d.state || {};
      const ind = d.individual || {};
      const amb = d.ambition;
      const men = d.mentorship || {};

      if (this.mode !== "focus" && window._map && st.place_id && window._map.venues[st.place_id]) {
        const v = window._map.venues[st.place_id];
        window._map.focusOn(v.x, v.y, 1.75);
        window._map.spotlightTarget = { x: v.x, y: v.y, radius: 26 };
      }

      const axesHtml = ind.axes
        ? Object.entries(ind.axes)
            .map(([k, v]) => `<span class="axis-pill">${tvEsc(k)}: ${v >= 0 ? "+" + v : v}</span>`)
            .join(" ")
        : "";
      const ambHtml = amb
        ? `<div class="ind-box"><b>🎯 Active Ambition:</b> ${tvEsc(amb.label)} (${Math.round((amb.progress || 0) * 100)}%)</div>`
        : "";
      const relsHtml = (d.relationships || []).slice(0, 6)
        .map(r => {
          const otherId = r.a_id === a.id ? r.b_id : r.a_id;
          return `<li class="click" data-other="${otherId}"><b>${tvEsc(r.label)}</b> ${tvEsc(r.other_name || r.name)} <span class="t">aff ${Math.round(r.affinity)} · fam ${Math.round(r.familiarity)}</span></li>`;
        })
        .join("");
      const memsHtml = (d.memories || []).slice(0, 6)
        .map(m => `<li class="mem ${m.kind === "reflection" ? "refl" : ""}">${tvEsc(m.text)}</li>`)
        .join("");

      body.innerHTML = `
        <div class="tv-insp-header">
          <h3>👤 ${tvEsc(a.name)} (${a.age}, ${tvEsc((a.occupation || "resident").replace(/_/g, " "))})</h3>
          <p class="persona">${tvEsc(a.persona || "")}</p>
        </div>
        <div style="margin:.4rem 0">
          <button class="tv-btn accent" id="tv-follow-res-btn">📌 Follow ${tvEsc(a.name.split(" ")[0])}</button>
          <button class="mini" id="tv-open-bonds-btn">🕸️ Bond wheel</button>
        </div>
        <div class="ind-box">
          <div><b>Archetype:</b> ${tvEsc(ind.archetype || "resident")} · <b>Chronotype:</b> ${tvEsc(ind.chronotype || "day_walker")} · <b>Knack:</b> ${tvEsc(ind.knack || "—")}</div>
          <div><b>Now:</b> ${tvEsc(st.activity || "idle")} (${tvEsc(st.mood || "ok")}) · Wallet $${Math.round((st.money_cents || 0) / 100).toLocaleString()}</div>
          ${men.household_tradition ? `<div><b>Family Tradition:</b> ${tvEsc(men.household_tradition)}</div>` : ""}
          <div class="axes-row">${axesHtml}</div>
        </div>
        ${ambHtml}
        <h4>Closest Bonds (click to jump)</h4>
        <ul>${relsHtml || "<li class='muted'>No close bonds yet</li>"}</ul>
        <h4>Recent Memories</h4>
        <ul>${memsHtml || "<li class='muted'>No memories yet</li>"}</ul>`;

      body.querySelectorAll("li[data-other]").forEach(li => {
        li.addEventListener("click", () => {
          this.pauseForInteraction("bond-jump");
          this.openResidentInspector(Number(li.dataset.other));
        });
      });
      body.querySelector("#tv-follow-res-btn")?.addEventListener("click", () => {
        this.follow("resident", a.id, a.name);
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
    body.innerHTML = `<div class="muted">Loading venue #${Number(placeId)}…</div>`;
    try {
      const venues = await api("/api/venues");
      const v = venues.find(x => Number(x.id) === Number(placeId));
      if (!v) {
        body.innerHTML = `<div class="muted">Venue not found.</div>`;
        return;
      }
      if (this.mode !== "focus" && window._map && window._map.venues[placeId]) {
        const mv = window._map.venues[placeId];
        window._map.focusOn(mv.x, mv.y, 1.55);
        window._map.spotlightTarget = { x: mv.x, y: mv.y, radius: 34 };
      }
      const occList = (v.occupants || [])
        .map(o => `<li class="click" data-aid="${o.id}"><b>${tvEsc(o.activity)}</b> ${tvEsc(o.name)} <span class="t">mood: ${tvEsc(o.mood)}</span></li>`)
        .join("");
      body.innerHTML = `
        <div class="tv-insp-header">
          <h3>🏛️ ${tvEsc(v.name)}</h3>
          <div class="muted">${tvEsc(v.kind.toUpperCase())} · ${tvEsc(v.district)} · Capacity ${v.capacity}</div>
        </div>
        <div style="margin:.4rem 0">
          <button class="tv-btn accent" id="tv-follow-ven-btn">📌 Watch this place</button>
        </div>
        <div class="ind-box"><b>${(v.occupants || []).length} residents currently inside</b></div>
        <h4>Current Occupants (click to inspect resident)</h4>
        <ul>${occList || "<li class='muted'>Nobody inside right now.</li>"}</ul>`;
      body.querySelector("#tv-follow-ven-btn")?.addEventListener("click", () => {
        this.follow("venue", v.id, v.name);
      });
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

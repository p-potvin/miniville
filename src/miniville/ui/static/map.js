/* Miniville town map — Pixi.js scene with 60fps TV Director camera, ambient
 * sky tinting, gentle venue breathing, and bounded featured movers (8-10 at
 * a time) so the village always feels alive without chaotic visual clutter. */
"use strict";

const MV_ACT_COLORS = {
  work: 0x5b8ff9, school: 0x4ecdc4, break: 0xf6c34c,
  eat: 0xf6a54c, eat_out: 0xf6a54c, shopping: 0xb28ef7,
  leisure: 0x6fd08c, social_call: 0x6fd08c, celebrate: 0xf26cae,
  home: 0x8a8f98, sleep: 0x3a3f4b, resting: 0xe05d5d, sick: 0xe05d5d,
  wallow: 0x9c4f4f, idle: 0x555a63,
};
const MV_KIND_COLORS = { public: 0x2e8b57, workplace: 0x3d6fb4, civic: 0xb89b3d };
const MV_LABEL = "#aab3c0", MV_PANEL = 0x1d2430, MV_EDGE = 0x35405a;

class TownMap {
  constructor(host) {
    this.host = host;
    this.app = new PIXI.Application({
      width: 1600, height: 900, backgroundColor: 0x141821,
      antialias: true, resolution: 1,
    });
    host.appendChild(this.app.view);
    this.app.view.style.width = "100%";
    this.app.view.style.height = "auto";

    this.world = new PIXI.Container();
    this.app.stage.addChild(this.world);

    this.venues = {};
    this.venuePulses = [];
    this.settledDots = [];
    this.tvMode = false;
    this.cameraTarget = null;
    this.spotlightTarget = null;
    this.moversData = [];
    this.hotspots = [];        // recent newsworthy events: ripples on the map
    this.flash = null;         // a breaking-news cut: one bright expanding ring
    this.moverPhase = 0;
    this.clock = 0;

    this._buildLayers();
    this.tooltip = this._makeTooltip();
    this._bindPanZoom();
    this.app.ticker.add(delta => this._animate(delta));
  }

  _buildLayers() {
    this.staticLayer = new PIXI.Container();
    this.pulseLayer = new PIXI.Graphics();
    this.moversGraphic = new PIXI.Graphics();
    this.moversLabels = new PIXI.Container();
    this.spotlightGraphic = new PIXI.Graphics();
    this.hotspotGraphic = new PIXI.Graphics();
    this.skyOverlay = new PIXI.Graphics();
    this.skyOverlay.eventMode = "none";

    this.world.addChild(this.staticLayer);
    this.world.addChild(this.pulseLayer);
    this.world.addChild(this.hotspotGraphic);
    this.world.addChild(this.moversGraphic);
    this.world.addChild(this.moversLabels);
    this.world.addChild(this.spotlightGraphic);
    this.app.stage.addChild(this.skyOverlay);
  }

  _makeTooltip() {
    const t = new PIXI.Text("", {
      fontFamily: "Consolas, monospace", fontSize: 14, fill: 0xffffff,
      stroke: 0x000000, strokeThickness: 3,
    });
    t.visible = false; t.zIndex = 20;
    this.app.stage.addChild(t);
    return t;
  }

  _bindPanZoom() {
    const stage = this.app.stage;
    let drag = null;
    stage.eventMode = "static";
    stage.hitArea = new PIXI.Rectangle(-1e5, -1e5, 2e5, 2e5);
    stage.on("pointerdown", e => {
      drag = { x: e.global.x, y: e.global.y };
      this.cameraTarget = null;
      if (window.mvUserInteracted) window.mvUserInteracted("map-drag");
    });
    stage.on("pointermove", e => {
      if (drag) {
        this.world.x += e.global.x - drag.x;
        this.world.y += e.global.y - drag.y;
        drag = { x: e.global.x, y: e.global.y };
      }
      if (this.tooltip.visible) {
        this.tooltip.position.set(e.global.x + 14, e.global.y + 10);
      }
    });
    const up = () => { drag = null; };
    stage.on("pointerup", up); stage.on("pointerupoutside", up);
    this.app.view.addEventListener("wheel", e => {
      e.preventDefault();
      this.cameraTarget = null;
      if (window.mvUserInteracted) window.mvUserInteracted("map-zoom");
      const k = e.deltaY < 0 ? 1.15 : 1 / 1.15;
      const s = Math.min(3, Math.max(0.4, this.world.scale.x * k));
      const px = e.offsetX * (1600 / (this.app.view.clientWidth || 1600));
      const py = e.offsetY * (900 / (this.app.view.clientHeight || 900));
      this.world.x = px - (px - this.world.x) * (s / this.world.scale.x);
      this.world.y = py - (py - this.world.y) * (s / this.world.scale.y);
      this.world.scale.set(s, s);
    }, { passive: false });
  }

  focusOn(worldX, worldY, scale = 1.5) {
    const s = Math.min(2.6, Math.max(0.75, scale));
    const tx = 800 - worldX * s;
    const ty = 450 - worldY * s;
    this.cameraTarget = { x: tx, y: ty, scale: s };
  }

  resetView() {
    this.cameraTarget = { x: 0, y: 0, scale: 1.0 };
  }

  setDirectorState(tvData) {
    if (!tvData) return;
    // Ambient sky tint
    if (tvData.sky) {
      const tintNum = parseInt((tvData.sky.tint_hex || "#1a2542").replace("#", ""), 16);
      const alpha = Number(tvData.sky.ambient_alpha ?? 0.12);
      this.skyOverlay.clear();
      this.skyOverlay.beginFill(tintNum, alpha).drawRect(0, 0, 1600, 900).endFill();
    }

    // Rebuild bounded featured movers (max 10 calm commuters gliding on curved arcs)
    this.moversData = (tvData.movers || []).slice(0, 10);
    this.moverPhase = 0;
    this.moversLabels.removeChildren().forEach(c => c.destroy({ children: true }));
    for (const m of this.moversData) {
      const tag = new PIXI.Text(`${m.name.split(" ")[0]} → ${m.to_label}`, {
        fontFamily: "Consolas, monospace",
        fontSize: 11,
        fontWeight: "bold",
        fill: 0xfff3c4,
        stroke: 0x0b0e17,
        strokeThickness: 3,
      });
      tag.anchor.set(0.5, 1.35);
      tag.eventMode = "static";
      tag.cursor = "pointer";
      tag.on("pointertap", e => {
        e.stopPropagation();
        if (window.mvUserInteracted) window.mvUserInteracted("mover-click");
        if (window.mvPick) window.mvPick(m);
      });
      this.moversLabels.addChild(tag);
      m._labelSprite = tag;
    }
  }

  _animate(delta) {
    this.clock += 0.025 * delta;

    // 1. Smooth camera pan/zoom interpolation
    if (this.cameraTarget) {
      const ease = Math.min(0.18, 0.042 * delta);
      const curS = this.world.scale.x;
      const nextS = curS + (this.cameraTarget.scale - curS) * ease;
      this.world.scale.set(nextS, nextS);
      this.world.x += (this.cameraTarget.x - this.world.x) * ease;
      this.world.y += (this.cameraTarget.y - this.world.y) * ease;
    }

    // 2. Gentle breathing pulse on active venues + subtle micro-sway on awake settled dots
    this.pulseLayer.clear();
    for (const vp of this.venuePulses) {
      const wave = 0.5 + 0.5 * Math.sin(this.clock * 1.4 + vp.id * 0.9);
      const pad = 4 + wave * 6;
      const alpha = 0.08 + (1 - wave) * 0.14;
      this.pulseLayer.lineStyle(1.5, vp.color, alpha)
        .drawRoundedRect(
          vp.x - vp.size / 2 - pad,
          vp.y - vp.size / 2 - pad,
          vp.size + pad * 2,
          vp.size + pad * 2,
          6
        );
    }

    // Subtle organic breathing for a small subset of awake settled residents
    if (this.settledDots.length) {
      for (let i = 0; i < this.settledDots.length; i += 3) {
        const d = this.settledDots[i];
        if (d.awake && d.sprite && !d.sprite.destroyed) {
          d.sprite.x = d.baseX + Math.cos(this.clock * 1.1 + d.phase) * 1.3;
          d.sprite.y = d.baseY + Math.sin(this.clock * 1.1 + d.phase) * 1.3;
        }
      }
    }

    // 3. Bounded Featured Movers (8-10 commuters gliding smoothly along curved paths)
    this.moversGraphic.clear();
    if (this.moversData.length) {
      this.moverPhase = (this.moverPhase + 0.0022 * delta) % 1.0;
      for (let i = 0; i < this.moversData.length; i++) {
        const m = this.moversData[i];
        // Stagger each mover slightly so they don't move in robotic lockstep
        const rawT = (this.moverPhase + i * 0.11) % 1.0;
        // Smoothstep easing
        const t = rawT * rawT * (3 - 2 * rawT);
        const dx = m.to_x - m.from_x;
        const dy = m.to_y - m.from_y;
        // Slight perpendicular arc so paths curve gracefully
        const bend = ((i % 2 === 0) ? 1 : -1) * 28 * Math.sin(Math.PI * t);
        const len = Math.hypot(dx, dy) || 1;
        const nx = -dy / len, ny = dx / len;
        const cx = m.from_x + dx * t + nx * bend;
        const cy = m.from_y + dy * t + ny * bend;

        const color = MV_ACT_COLORS[m.activity] ?? 0xe8b64c;

        // Soft dashed route line & comet tail
        this.moversGraphic.lineStyle(1.2, color, 0.22)
          .moveTo(m.from_x, m.from_y)
          .lineTo(cx, cy);

        for (let step = 1; step <= 4; step++) {
          const tt = Math.max(0, t - step * 0.025);
          const tbend = ((i % 2 === 0) ? 1 : -1) * 28 * Math.sin(Math.PI * tt);
          const tx = m.from_x + dx * tt + nx * tbend;
          const ty = m.from_y + dy * tt + ny * tbend;
          this.moversGraphic.lineStyle(0)
            .beginFill(color, 0.35 - step * 0.07)
            .drawCircle(tx, ty, 4.2 - step * 0.6)
            .endFill();
        }

        // Outer halo + crisp traveler dot
        this.moversGraphic.lineStyle(1.5, 0xffffff, 0.85)
          .beginFill(color, 1.0)
          .drawCircle(cx, cy, 5.2)
          .endFill();

        if (m._labelSprite && !m._labelSprite.destroyed) {
          m._labelSprite.position.set(cx, cy - 4);
        }
      }
    }

    // 3b. Story hotspots: where something newsworthy happened in the last few
    // hours, a slow ripple whose strength follows the story and fades with age
    this.hotspotGraphic.clear();
    if (this.tvMode && this.hotspots && this.hotspots.length) {
      for (let i = 0; i < this.hotspots.length; i++) {
        const h = this.hotspots[i];
        const fresh = Math.max(0.25, 1 - (h.age || 0) / 6);
        const strength = Math.min(1, (h.score || 30) / 70);
        const color = (h.score || 0) >= 60 ? 0xe05d5d : 0xe8b64c;
        for (let ring = 0; ring < 2; ring++) {
          const phase = ((this.clock * 0.35 + i * 0.37 + ring * 0.5) % 1);
          const r = 8 + phase * (22 + strength * 22);
          this.hotspotGraphic.lineStyle(2, color, (1 - phase) * 0.7 * fresh * strength)
            .drawCircle(h.x, h.y, r);
        }
        this.hotspotGraphic.lineStyle(0).beginFill(color, 0.8 * fresh).drawCircle(h.x, h.y, 3).endFill();
      }
    }
    if (this.flash) {
      this.flash.t += delta / 60;
      const t = this.flash.t;
      if (t > 1.6) {
        this.flash = null;
      } else {
        this.hotspotGraphic.lineStyle(4, 0xff6b6b, Math.max(0, 1 - t / 1.6))
          .drawCircle(this.flash.x, this.flash.y, 12 + t * 90);
      }
    }

    // 4. Spotlight target golden focus ring
    this.spotlightGraphic.clear();
    if (this.spotlightTarget) {
      const pulse = 0.5 + 0.5 * Math.sin(this.clock * 2.4);
      const r = (this.spotlightTarget.radius || 26) + pulse * 8;
      this.spotlightGraphic.lineStyle(2.5, 0xe8b64c, 0.85 - pulse * 0.35)
        .drawCircle(this.spotlightTarget.x, this.spotlightTarget.y, r);
      this.spotlightGraphic.lineStyle(1.2, 0xfff3c4, 0.45)
        .drawCircle(this.spotlightTarget.x, this.spotlightTarget.y, r + 6);
    }
  }

  async refresh() {
    const data = await api("/api/map");
    this._draw(data);
  }

  _draw(data) {
    this.staticLayer.removeChildren().forEach(c => c.destroy({ children: true }));
    this.venuePulses = [];
    this.settledDots = [];
    this._legendDone = false;
    const g = new PIXI.Graphics();
    this.staticLayer.addChild(g);

    const zones = {};
    const savings = data.districts.filter(d => d.profile)
      .map(d => d.profile.median_savings_cents);
    const lo = Math.min(...savings), hi = Math.max(...savings);
    for (const d of data.districts) {
      zones[d.name] = d;
      g.lineStyle(1.5, MV_EDGE, 1).beginFill(MV_PANEL, 0.85)
        .drawRoundedRect(d.x, d.y, d.w, d.h, 14).endFill();
      if (d.profile && hi > lo) {
        const t = (d.profile.median_savings_cents - lo) / (hi - lo);
        g.lineStyle(0).beginFill(0xd4a72c, 0.04 + 0.16 * t)
          .drawRoundedRect(d.x, d.y, d.w, d.h, 14).endFill();
      }
      if (d.homes) {
        const hb = new PIXI.Graphics();
        hb.lineStyle(1, MV_EDGE, 0.8).beginFill(0x171c26, 0.9)
          .drawRoundedRect(d.x + 8, d.y + d.h - 78, d.w - 16, 70, 8).endFill();
        this.staticLayer.addChild(hb);
        const ht = new PIXI.Text(`homes · ${d.homes}`, {
          fontFamily: "Consolas, monospace", fontSize: 11, fill: 0x66707f,
        });
        ht.position.set(d.x + 14, d.y + d.h - 24);
        this.staticLayer.addChild(ht);
        d._homesRect = { x: d.x + 16, y: d.y + d.h - 70, w: d.w - 32, h: 44 };
      }
      if (d.profile) {
        const p = d.profile;
        const info = new PIXI.Text(
          `rent $${Math.round(p.rent_cents / 100)}/wk · ${p.residents} res · ` +
          `median savings $${Math.round(p.median_savings_cents / 100).toLocaleString()}`, {
            fontFamily: "Consolas, monospace", fontSize: 11, fill: 0xb8a65a,
          });
        info.position.set(d.x + 110, d.y + d.h - 24);
        this.staticLayer.addChild(info);
      }
      const name = new PIXI.Text(d.name.toUpperCase(), {
        fontFamily: "Consolas, monospace", fontSize: 15,
        fontWeight: "bold", fill: 0x7784a0,
      });
      name.position.set(d.x + 10, d.y + 8);
      this.staticLayer.addChild(name);
    }

    const byVenue = {}, byDistrict = {};
    for (const a of data.agents) {
      if (data.places.some(p => p.id === a.place)) (byVenue[a.place] ||= []).push(a);
      else (byDistrict[a.hdist || "_other"] ||= []).push(a);
    }

    // venues
    for (const p of data.places) {
      const size = 16 + Math.sqrt(p.capacity || 30) * 2.4;
      const color = MV_KIND_COLORS[p.kind] || 0x556;
      const vg = new PIXI.Graphics();
      const alpha = p.closed ? 0.25 : 1;
      vg.lineStyle(2, p.closed ? 0xe05d5d : MV_EDGE, 1)
        .beginFill(color, p.closed ? 0.35 : 0.9)
        .drawRect(-size / 2, -size / 2, size, size).endFill();
      vg.position.set(p.x, p.y); vg.alpha = alpha;
      vg.eventMode = "static"; vg.cursor = "pointer";
      vg.on("pointertap", e => {
        e.stopPropagation();
        if (window.mvUserInteracted) window.mvUserInteracted("venue-click");
        if (window.mvInspectVenue) window.mvInspectVenue(p.id);
      });
      this.staticLayer.addChild(vg);
      const label = new PIXI.Text(
        (p.closed ? "🔥 " : "") + p.name, {
          fontFamily: "Consolas, monospace", fontSize: 12,
          fill: p.closed ? 0xe08a8a : MV_LABEL,
        });
      label.anchor.set(0.5, 0);
      label.position.set(p.x, p.y + size / 2 + 4);
      this.staticLayer.addChild(label);
      this.venues[p.id] = { p, x: p.x, y: p.y, size };

      const crowdCount = (byVenue[p.id] || []).length;
      if (!p.closed && crowdCount > 0) {
        this.venuePulses.push({ id: p.id, x: p.x, y: p.y, size, color, count: crowdCount });
      }
    }

    const dotTex = this._dotTexture();
    const moverIds = new Set((this.moversData || []).map(m => m.id));

    for (const [pid, list] of Object.entries(byVenue)) {
      const v = this.venues[pid];
      if (!v) continue;
      if (list.length) {
        const n = new PIXI.Text(String(list.length), {
          fontFamily: "Consolas, monospace", fontSize: 13,
          fontWeight: "bold", fill: 0xffffff,
          stroke: 0x000000, strokeThickness: 4,
        });
        n.anchor.set(0.5, 1);
        n.position.set(v.x, v.y - v.size / 2 - 3);
        this.staticLayer.addChild(n);
      }
      list.forEach((a, i) => {
        if (this.tvMode && moverIds.has(a.id)) return;
        const r = v.size / 2 + 8 + Math.floor(i / 14) * 8;
        const ang = i * 2.399963;
        const bx = v.x + r * Math.cos(ang);
        const by = v.y + r * Math.sin(ang);
        const sp = this._dot(dotTex, bx, by, a, this.tvMode ? 0.68 : 0.95);
        this.settledDots.push({
          sprite: sp, baseX: bx, baseY: by,
          phase: (a.id * 0.7) % 6.28,
          awake: a.activity !== "sleep",
        });
      });
    }
    for (const [dname, list] of Object.entries(byDistrict)) {
      const zone = zones[dname] || zones._other || data.districts.at(-1);
      const rect = zone._homesRect || { x: zone.x + 16, y: zone.y + zone.h - 60, w: zone.w - 32, h: 40 };
      if (list.length) {
        const n = new PIXI.Text(`${list.length} home`, {
          fontFamily: "Consolas, monospace", fontSize: 11,
          fontWeight: "bold", fill: 0xb8c0d0,
        });
        n.anchor.set(1, 1);
        n.position.set(zone.x + zone.w - 12, zone.y + zone.h - 22);
        this.staticLayer.addChild(n);
      }
      const cols = Math.max(1, Math.floor(rect.w / 12));
      list.forEach((a, i) => {
        if (this.tvMode && moverIds.has(a.id)) return;
        const cx = rect.x + 6 + (i % cols) * 12;
        const cy = rect.y + 6 + Math.floor(i / cols) * 10;
        this._dot(dotTex, cx, cy, a, this.tvMode ? 0.38 : 0.85);
      });
    }

    this._legend();
  }

  _dotTexture() {
    if (this._tex) return this._tex;
    const g = new PIXI.Graphics();
    g.beginFill(0xffffff).drawCircle(0, 0, 3.4).endFill();
    this._tex = this.app.renderer.generateTexture(g);
    return this._tex;
  }

  _dot(tex, x, y, agent, alpha = 0.9) {
    const s = new PIXI.Sprite(tex);
    s.anchor.set(0.5);
    s.position.set(x, y);
    s.alpha = alpha;
    s.tint = MV_ACT_COLORS[agent.activity] ?? 0x999999;
    s.eventMode = "static"; s.cursor = "pointer";
    s.on("pointerover", e => {
      this.tooltip.text = `${agent.name} — ${agent.activity} (${agent.mood})`;
      this.tooltip.visible = true;
      const pt = e.data.getLocalPosition(this.app.stage);
      this.tooltip.position.set(pt.x + 14, pt.y + 10);
    });
    s.on("pointerout", () => { this.tooltip.visible = false; });
    s.on("pointertap", e => {
      e.stopPropagation();
      if (window.mvUserInteracted) window.mvUserInteracted("dot-click");
      if (window.mvPick) window.mvPick(agent);
    });
    this.staticLayer.addChild(s);
    return s;
  }

  _legend() {
    if (this._legendDone) return;
    this._legendDone = true;
    const entries = Object.entries(MV_ACT_COLORS).filter(([k]) =>
      ["work", "school", "eat_out", "leisure", "celebrate",
       "home", "resting"].includes(k));
    const lg = new PIXI.Container();
    let lx = 0;
    for (const [act, color] of entries) {
      const sw = new PIXI.Graphics();
      sw.beginFill(color).drawCircle(0, 0, 4).endFill();
      sw.position.set(lx, 5); lg.addChild(sw);
      const t = new PIXI.Text(act, {
        fontFamily: "Consolas, monospace", fontSize: 11, fill: 0x8a93a5,
      });
      t.position.set(lx + 8, -2); lg.addChild(t);
      lx += 14 + t.width;
    }
    lg.position.set(640, 880);
    this.staticLayer.addChild(lg);
  }
}

let _map = null;
window._map = null;
async function renderMap(host) {
  if (!_map) _map = window._map = new TownMap(host);
  await _map.refresh();
}

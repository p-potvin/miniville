/* Miniville town map — Pixi.js scene.
 * Districts are fixed tiles; venues are squares scaled by capacity; every
 * living resident is a dot, coloured by what they're doing right now. */
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
    this.tooltip = this._makeTooltip();
    this._bindPanZoom();
    this._buildStatic();
  }

  _makeTooltip() {
    const t = new PIXI.Text("", {
      fontFamily: "Consolas, monospace", fontSize: 14, fill: 0xffffff,
      stroke: 0x000000, strokeThickness: 3,
    });
    t.visible = false; t.zIndex = 10;
    this.app.stage.addChild(t);
    return t;
  }

  _bindPanZoom() {
    const stage = this.app.stage;
    let drag = null;
    stage.eventMode = "static";
    stage.hitArea = new PIXI.Rectangle(-1e5, -1e5, 2e5, 2e5);
    stage.on("pointerdown", e => { drag = { x: e.global.x, y: e.global.y }; });
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
      const k = e.deltaY < 0 ? 1.15 : 1 / 1.15;
      const s = Math.min(3, Math.max(0.4, this.world.scale.x * k));
      // zoom around the cursor
      const px = e.offsetX, py = e.offsetY;
      this.world.x = px - (px - this.world.x) * (s / this.world.scale.x);
      this.world.y = py - (py - this.world.y) * (s / this.world.scale.y);
      this.world.scale.set(s, s);
    }, { passive: false });
  }

  _buildStatic() {
    this.staticLayer = new PIXI.Container();
    this.world.addChild(this.staticLayer);
  }

  async refresh() {
    const data = await api("/api/map");
    this._draw(data);
  }

  _draw(data) {
    this.staticLayer.removeChildren().forEach(c => c.destroy({ children: true }));
    const g = new PIXI.Graphics();
    this.staticLayer.addChild(g);

    // district tiles
    const zones = {};
    for (const d of data.districts) {
      zones[d.name] = d;
      g.lineStyle(1.5, MV_EDGE, 1).beginFill(MV_PANEL, 0.85)
        .drawRoundedRect(d.x, d.y, d.w, d.h, 14).endFill();
      const name = new PIXI.Text(d.name.toUpperCase(), {
        fontFamily: "Consolas, monospace", fontSize: 15,
        fontWeight: "bold", fill: 0x7784a0,
      });
      name.position.set(d.x + 10, d.y + 8);
      this.staticLayer.addChild(name);
      // homes block: bottom strip of the tile
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
    }

    // agent dots: scatter around their venue; the home crowd fills the
    // district's homes block
    const byVenue = {}, byDistrict = {};
    for (const a of data.agents) {
      if (this.venues[a.place]) (byVenue[a.place] ||= []).push(a);
      else (byDistrict[a.hdist || "_other"] ||= []).push(a);
    }

    const dotTex = this._dotTexture();
    for (const [pid, list] of Object.entries(byVenue)) {
      const v = this.venues[pid];
      list.forEach((a, i) => {
        const r = v.size / 2 + 8 + Math.floor(i / 14) * 8;
        const ang = i * 2.399963;                    // golden angle scatter
        this._dot(dotTex, v.x + r * Math.cos(ang), v.y + r * Math.sin(ang), a);
      });
    }
    for (const [dname, list] of Object.entries(byDistrict)) {
      const zone = zones[dname] || zones._other || data.districts.at(-1);
      const rect = zone._homesRect || { x: zone.x + 16, y: zone.y + zone.h - 60, w: zone.w - 32, h: 40 };
      const cols = Math.max(1, Math.floor(rect.w / 12));
      list.forEach((a, i) => {
        const cx = rect.x + 6 + (i % cols) * 12;
        const cy = rect.y + 6 + Math.floor(i / cols) * 10;
        this._dot(dotTex, cx, cy, a);
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

  _dot(tex, x, y, agent) {
    const s = new PIXI.Sprite(tex);
    s.anchor.set(0.5);
    s.position.set(x, y);
    s.tint = MV_ACT_COLORS[agent.activity] ?? 0x999999;
    s.eventMode = "static"; s.cursor = "pointer";
    s.on("pointerover", e => {
      this.tooltip.text = `${agent.name} — ${agent.activity} (${agent.mood})`;
      this.tooltip.visible = true;
      const pt = e.data.getLocalPosition(this.app.stage);
      this.tooltip.position.set(pt.x + 14, pt.y + 10);
    });
    s.on("pointerout", () => { this.tooltip.visible = false; });
    s.on("pointertap", () => { if (window.mvPick) window.mvPick(agent); });
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

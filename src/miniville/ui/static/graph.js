/* Miniville bond & town network graph on Pixi.
 * Supports two modes:
 * 1. Town Network (`showTownNetwork`): district-clustered social network of
 *    spouses/partners, friends, mentors/apprentices, and rivals.
 * 2. Ego Wheel (`show`): radial 2-ring ego network around a single resident. */
"use strict";

const BG_LABEL_COLORS = {
  spouse: 0xff5a5a, partner: 0xf26cae, sweetheart: 0xf291b5,
  close_friend: 0xf6c34c, friend: 0x6fd08c, kin: 0x4ecdc4,
  mentorship: 0x38bdf8,
  rival: 0xb23a3a, estranged: 0x6b7280, acquaintance: 0x7c8698,
};
const BG_DISTRICT_CENTERS = {
  "Lakeshore":        [230, 195],
  "Downtown":         [490, 350],
  "Old Mill Quarter": [750, 195],
  "Greenhill":        [230, 520],
  "The Flats":        [750, 520],
};
const BG_DISTRICT_COLORS = {
  "Lakeshore": 0x38bdf8,
  "Downtown": 0xf6c34c,
  "Old Mill Quarter": 0xa78bfa,
  "Greenhill": 0x4ade80,
  "The Flats": 0xf87171,
};
const BG_SEX = { Male: 0x5b8ff9, Female: 0xf26cae };
const BG_W = 980, BG_H = 700, BG_CX = BG_W / 2, BG_CY = BG_H / 2;
const BG_R1 = 190, BG_R2 = 320;

class BondGraph {
  constructor(host) {
    this.host = host;
    this.mode = "town";
    this.currentAgentId = null;
    this.app = new PIXI.Application({
      width: BG_W, height: BG_H, backgroundColor: 0x141821, antialias: true,
    });
    host.appendChild(this.app.view);
    this.app.view.style.width = "100%";
    this.app.view.style.height = "auto";
    this.world = new PIXI.Container();
    this.app.stage.addChild(this.world);
    this.tooltip = new PIXI.Text("", {
      fontFamily: "Consolas, monospace", fontSize: 13, fill: 0xffffff,
      stroke: 0x000000, strokeThickness: 3,
    });
    this.tooltip.visible = false;
    this.app.stage.addChild(this.tooltip);
    this._pan();
  }

  _pan() {
    const s = this.app.stage;
    let drag = null;
    s.eventMode = "static";
    s.hitArea = new PIXI.Rectangle(-1e5, -1e5, 2e5, 2e5);
    s.on("pointerdown", e => { drag = { x: e.global.x, y: e.global.y }; });
    s.on("pointermove", e => {
      if (drag) {
        this.world.x += e.global.x - drag.x;
        this.world.y += e.global.y - drag.y;
        drag = { x: e.global.x, y: e.global.y };
      }
      if (this.tooltip.visible)
        this.tooltip.position.set(e.global.x + 12, e.global.y + 8);
    });
    const up = () => { drag = null; };
    s.on("pointerup", up); s.on("pointerupoutside", up);
  }

  async show(agentId) {
    this.mode = "ego";
    this.currentAgentId = agentId;
    this.world.position.set(0, 0);
    const g = await api("/api/graph/" + agentId);
    if (g.error) return;
    this._drawEgo(g);
  }

  async showTown(filterKind = "all") {
    this.mode = "town";
    this.world.position.set(0, 0);
    const g = await api("/api/network?filter_kind=" + encodeURIComponent(filterKind));
    this._drawTown(g);
  }

  _drawTown(g) {
    this.world.removeChildren().forEach(c => c.destroy({ children: true }));
    const nodes = g.nodes || [];
    const edges = g.edges || [];
    const byDist = {};
    for (const n of nodes) {
      const d = n.district || "Downtown";
      (byDist[d] ||= []).push(n);
    }

    // Draw district cluster halos and titles
    const bg = new PIXI.Graphics();
    this.world.addChild(bg);
    for (const [dname, [cx, cy]] of Object.entries(BG_DISTRICT_CENTERS)) {
      const col = BG_DISTRICT_COLORS[dname] ?? 0x64748b;
      bg.lineStyle(1.5, col, 0.35).beginFill(col, 0.06).drawCircle(cx, cy, 132).endFill();
      const dt = new PIXI.Text(dname, {
        fontFamily: "Consolas, monospace", fontSize: 12, fontWeight: "bold",
        fill: col,
      });
      dt.anchor.set(0.5, 0.5);
      dt.position.set(cx, cy - 116);
      this.world.addChild(dt);
    }

    // Golden-angle spiral inside each district cluster, highest degree near center
    const pos = {};
    const golden = Math.PI * (3 - Math.sqrt(5));
    for (const [dname, list] of Object.entries(byDist)) {
      const [cx, cy] = BG_DISTRICT_CENTERS[dname] || [BG_CX, BG_CY];
      list.sort((a, b) => (b.degree || 0) - (a.degree || 0));
      list.forEach((n, idx) => {
        const rad = idx === 0 ? 0 : 18 + Math.sqrt(idx / Math.max(1, list.length)) * 96;
        const theta = idx * golden;
        pos[n.id] = [cx + rad * Math.cos(theta), cy + rad * Math.sin(theta)];
      });
    }

    const eg = new PIXI.Graphics();
    this.world.addChild(eg);
    for (const e of edges) {
      const pa = pos[e.a_id], pb = pos[e.b_id];
      if (!pa || !pb) continue;
      const col = BG_LABEL_COLORS[e.label] ?? 0x556070;
      const w = e.label === "mentorship" ? 2.2 : (0.9 + Math.min(2.2, Math.abs(e.affinity || 0) / 45));
      const alpha = e.label === "mentorship" ? 0.85 : 0.55;
      eg.lineStyle(w, col, alpha).moveTo(pa[0], pa[1]).lineTo(pb[0], pb[1]);
    }

    for (const n of nodes) {
      const p = pos[n.id];
      if (!p) continue;
      const [x, y] = p;
      const deg = n.degree || 1;
      const r = Math.min(11, 4.5 + Math.sqrt(deg) * 1.4);
      const dcol = BG_DISTRICT_COLORS[n.district] ?? 0xffffff;
      const ng = new PIXI.Graphics();
      ng.lineStyle(1.5, dcol, 0.85)
        .beginFill(BG_SEX[n.sex] ?? 0x888, 0.95)
        .drawCircle(0, 0, r).endFill();
      ng.position.set(x, y);
      ng.eventMode = "static"; ng.cursor = "pointer";
      ng.on("pointerover", e => {
        this.tooltip.text = `${n.name} (${n.district} · ${deg} ties · click for ego wheel)`;
        this.tooltip.visible = true;
        const pt = e.data.getLocalPosition(this.app.stage);
        this.tooltip.position.set(pt.x + 12, pt.y + 8);
      });
      ng.on("pointerout", () => { this.tooltip.visible = false; });
      ng.on("pointertap", () => this.show(n.id));
      this.world.addChild(ng);

      if (deg >= 4) {
        const t = new PIXI.Text(n.name.split(" ")[0], {
          fontFamily: "Consolas, monospace", fontSize: 10, fill: 0xcbd5e1,
        });
        t.anchor.set(0.5, 0);
        t.position.set(x, y + r + 2);
        this.world.addChild(t);
      }
    }
  }

  _drawEgo(g) {
    this.world.removeChildren().forEach(c => c.destroy({ children: true }));
    const pos = {};
    pos[g.ego.id] = [BG_CX, BG_CY];
    const ring1 = g.nodes.filter(n => n.ring === 1);
    const famOf = {};
    for (const e of g.edges) {
      if (e.a_id === g.ego.id) famOf[e.b_id] = e.familiarity;
      if (e.b_id === g.ego.id) famOf[e.a_id] = e.familiarity;
    }
    ring1.sort((a, b) => (famOf[b.id] || 0) - (famOf[a.id] || 0));
    const angle = {};
    ring1.forEach((n, i) => {
      const a = -Math.PI / 2 + (i / Math.max(1, ring1.length)) * Math.PI * 2;
      angle[n.id] = a;
      pos[n.id] = [BG_CX + BG_R1 * Math.cos(a), BG_CY + BG_R1 * Math.sin(a)];
    });
    const ring2 = g.nodes.filter(n => n.ring === 2);
    const clusters = {};
    for (const n of ring2) {
      let best = null, bf = -1;
      for (const e of g.edges) {
        const other = e.a_id === n.id ? e.b_id
                    : e.b_id === n.id ? e.a_id : null;
        if (other != null && angle[other] !== undefined
            && e.familiarity > bf) { best = other; bf = e.familiarity; }
      }
      (clusters[best ?? "_"] ||= []).push(n);
    }
    for (const [pid, members] of Object.entries(clusters)) {
      const base = angle[pid] ?? 0;
      members.forEach((n, i) => {
        const a = base + ((i - (members.length - 1) / 2) * 0.14);
        pos[n.id] = [BG_CX + BG_R2 * Math.cos(a), BG_CY + BG_R2 * Math.sin(a)];
      });
    }

    const eg = new PIXI.Graphics();
    this.world.addChild(eg);
    for (const e of g.edges) {
      if (!pos[e.a_id] || !pos[e.b_id]) continue;
      const w = 0.8 + Math.min(2.4, Math.abs(e.affinity || 0) / 45);
      eg.lineStyle(w, BG_LABEL_COLORS[e.label] ?? 0x556070, 0.75)
        .moveTo(pos[e.a_id][0], pos[e.a_id][1])
        .lineTo(pos[e.b_id][0], pos[e.b_id][1]);
    }

    for (const n of g.nodes) {
      const [x, y] = pos[n.id];
      const r = n.ring === 0 ? 13 : n.ring === 1 ? 9 : 6;
      const ng = new PIXI.Graphics();
      ng.lineStyle(n.ring === 0 ? 3 : 1.5,
                   n.ring === 0 ? 0xf6c34c : 0xffffff,
                   n.ring === 0 ? 1 : 0.55)
        .beginFill(BG_SEX[n.sex] ?? 0x888, n.ring === 2 ? 0.55 : 1)
        .drawCircle(0, 0, r).endFill();
      ng.position.set(x, y);
      ng.eventMode = "static"; ng.cursor = "pointer";
      ng.on("pointerover", e => {
        this.tooltip.text = n.name;
        this.tooltip.visible = true;
        const p = e.data.getLocalPosition(this.app.stage);
        this.tooltip.position.set(p.x + 12, p.y + 8);
      });
      ng.on("pointerout", () => { this.tooltip.visible = false; });
      ng.on("pointertap", () => this.show(n.id));
      this.world.addChild(ng);
      if (n.ring < 2) {
        const t = new PIXI.Text(n.name.split(" ")[0], {
          fontFamily: "Consolas, monospace", fontSize: n.ring ? 11 : 13,
          fontWeight: n.ring ? "normal" : "bold",
          fill: n.ring ? 0x9aa4b8 : 0xf6c34c,
        });
        t.anchor.set(0.5, 0);
        t.position.set(x, y + r + 3);
        this.world.addChild(t);
      }
    }
  }
}

let _bond = null;
window._bond = null;
async function renderBond(host, agentId) {
  if (!_bond) _bond = window._bond = new BondGraph(host);
  await _bond.show(agentId);
}
async function renderTownNetwork(host, filterKind = "all") {
  if (!_bond) _bond = window._bond = new BondGraph(host);
  await _bond.showTown(filterKind);
}

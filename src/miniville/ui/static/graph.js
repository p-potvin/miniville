/* Miniville bond graph — radial ego network on Pixi.
 * Ego at centre, partners on ring 1 (sorted by familiarity, clockwise from
 * the strongest), partners' partners on ring 2 near their parent angle.
 * Click a node to re-centre on that resident. */
"use strict";

const BG_LABEL_COLORS = {
  spouse: 0xff5a5a, partner: 0xf26cae, sweetheart: 0xf291b5,
  close_friend: 0xf6c34c, friend: 0x6fd08c, kin: 0x4ecdc4,
  rival: 0xb23a3a, estranged: 0x6b7280, acquaintance: 0x7c8698,
};
const BG_SEX = { Male: 0x5b8ff9, Female: 0xf26cae };
const BG_W = 980, BG_H = 700, BG_CX = BG_W / 2, BG_CY = BG_H / 2;
const BG_R1 = 190, BG_R2 = 320;

class BondGraph {
  constructor(host) {
    this.host = host;
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
    const g = await api("/api/graph/" + agentId);
    if (g.error) return;
    this._draw(g);
  }

  _draw(g) {
    this.world.removeChildren().forEach(c => c.destroy({ children: true }));
    const byId = {};
    for (const n of g.nodes) byId[n.id] = n;

    // positions: ego centre; ring 1 evenly spread sorted by familiarity;
    // ring 2 clusters near the angle of its strongest ring-1 link
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
      const a = -Math.PI / 2 + (i / ring1.length) * Math.PI * 2;
      angle[n.id] = a;
      pos[n.id] = [BG_CX + BG_R1 * Math.cos(a), BG_CY + BG_R1 * Math.sin(a)];
    });
    const ring2 = g.nodes.filter(n => n.ring === 2);
    // a ring-2 node sits at the angle of its strongest ring-1 edge, offset
    // outward; siblings fan out slightly around that angle
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
      const base = angle[pid] ?? (Math.random() * Math.PI * 2);
      members.forEach((n, i) => {
        const a = base + ((i - (members.length - 1) / 2) * 0.14);
        pos[n.id] = [BG_CX + BG_R2 * Math.cos(a), BG_CY + BG_R2 * Math.sin(a)];
      });
    }

    // edges
    const eg = new PIXI.Graphics();
    this.world.addChild(eg);
    for (const e of g.edges) {
      if (!pos[e.a_id] || !pos[e.b_id]) continue;
      const w = 0.8 + Math.min(2.4, (e.affinity || 0) / 45);
      eg.lineStyle(w, BG_LABEL_COLORS[e.label] ?? 0x556070, 0.75)
        .moveTo(pos[e.a_id][0], pos[e.a_id][1])
        .lineTo(pos[e.b_id][0], pos[e.b_id][1]);
    }

    // nodes
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

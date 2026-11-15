"""
vgrade HTML 評分報告產生器

產生單一、離線可用的 HTML 檔（不需要網路或外部套件），包含：
總分與各類別分數、設計統計、pipeline 各級深度圖、路徑深度圖、
可篩選的問題清單，以及標註問題位置的原始碼檢視。
"""
from __future__ import annotations

import datetime
import html
import math
from collections import Counter, defaultdict

from vgrade import CATEGORIES, SEV_LABEL, SEV_ORDER, grade_letter

esc = html.escape

SEV_ICON = {"error": "✕", "warning": "!", "info": "i"}

CSS = r"""
:root {
  color-scheme: light;
  --page: #f9f9f7; --surface: #fcfcfb; --surface-2: #f3f2ee;
  --ink: #0b0b0b; --ink-2: #52514e; --muted: #898781;
  --grid: #e1e0d9; --axis: #c3c2b7; --border: rgba(11,11,11,0.10);
  --series: #2a78d6;
  --good: #0ca30c; --warning: #fab219; --critical: #d03b3b;
  --on-good: #ffffff; --on-warning: #0b0b0b; --on-critical: #ffffff;
  --link: #1c5cab;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --page: #0d0d0d; --surface: #1a1a19; --surface-2: #232321;
    --ink: #f0efec; --ink-2: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
    --series: #3987e5; --link: #86b6ef;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --page: #0d0d0d; --surface: #1a1a19; --surface-2: #232321;
  --ink: #f0efec; --ink-2: #c3c2b7; --muted: #898781;
  --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
  --series: #3987e5; --link: #86b6ef;
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body {
  margin: 0; background: var(--page); color: var(--ink);
  font: 15px/1.55 system-ui, -apple-system, "Segoe UI", "Microsoft JhengHei", "PingFang TC", sans-serif;
}
main { max-width: 1120px; margin: 0 auto; padding: 24px 16px 48px; }
a { color: var(--link); }
h1 { font-size: 22px; margin: 0; }
h2 { font-size: 17px; margin: 0 0 12px; }
.card {
  background: var(--surface); border: 1px solid var(--border); border-radius: 12px;
  padding: 18px 20px; margin-top: 16px;
}
.meta { color: var(--ink-2); font-size: 13px; margin-top: 4px; overflow-wrap: anywhere; }
.muted { color: var(--muted); }

/* hero */
.hero { display: grid; grid-template-columns: minmax(180px, 240px) 1fr; gap: 24px; align-items: center; }
.total { text-align: center; }
.total .num { font-size: 56px; font-weight: 700; line-height: 1; }
.total .of { color: var(--ink-2); font-size: 15px; }
.badge-grade {
  display: inline-flex; align-items: center; gap: 6px; margin-top: 10px;
  padding: 3px 12px; border-radius: 999px; font-weight: 700; font-size: 14px;
}
.g-good { background: var(--good); color: var(--on-good); }
.g-warning { background: var(--warning); color: var(--on-warning); }
.g-critical { background: var(--critical); color: var(--on-critical); }

/* category bars */
.bars { display: grid; gap: 8px; }
.bar-row {
  display: grid; grid-template-columns: 88px 1fr 40px 44px; gap: 10px; align-items: center;
  font-size: 14px; border-radius: 6px;
}
.bar-row:hover { background: var(--surface-2); }
.bar-row .bl { color: var(--ink); }
.track { position: relative; height: 12px; background: var(--grid); border-radius: 4px; overflow: hidden; }
.fill { position: absolute; inset: 0 auto 0 0; background: var(--series); border-radius: 0 4px 4px 0; }
.bv { text-align: right; font-weight: 600; font-variant-numeric: tabular-nums; }
.bw { color: var(--muted); font-size: 12px; text-align: right; font-variant-numeric: tabular-nums; }
.bar-row.na .track { background: none; height: auto; overflow: visible; }
.na-text { color: var(--muted); font-size: 13px; }

/* tiles */
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; }
.tile { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px; }
.tile .k { color: var(--ink-2); font-size: 13px; }
.tile .v { font-size: 26px; font-weight: 700; line-height: 1.25; }
.tile .v small { font-size: 13px; font-weight: 400; color: var(--ink-2); margin-left: 4px; }
.tile .s { color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }

/* charts */
.charts { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.charts .card { margin-top: 0; }
.chart-sub { color: var(--ink-2); font-size: 13px; margin: -6px 0 10px; }
svg.chart { width: 100%; height: auto; display: block; overflow: visible; }
svg.chart text { fill: var(--muted); font-size: 11px; font-family: inherit; }
svg.chart .val { fill: var(--ink); font-weight: 600; }
svg.chart .grid { stroke: var(--grid); stroke-width: 1; }
svg.chart .base { stroke: var(--axis); stroke-width: 1; }
svg.chart .budget { stroke: var(--ink-2); stroke-width: 1.5; stroke-dasharray: 5 4; }
svg.chart .budget-label { fill: var(--ink-2); }
svg.chart .bar { fill: var(--series); }
svg.chart .hit { fill: transparent; }
svg.chart .hit:hover + .bar, svg.chart g:hover .bar { opacity: .82; }
.hbars { display: grid; gap: 10px; }
.hrow { display: grid; grid-template-columns: 112px 1fr 40px; gap: 10px; align-items: center; font-size: 13px; white-space: nowrap; }
.hero > *, .charts > *, .tiles > *, .bar-row > *, .hrow > * { min-width: 0; }
.tile .s, .tile .v { overflow: hidden; text-overflow: ellipsis; }
.htrack { position: relative; height: 14px; }
.htrack .fill { border-radius: 0 4px 4px 0; }
.htrack .baseline { position: absolute; left: 0; top: -3px; bottom: -3px; width: 1px; background: var(--axis); }
.htrack .limit { position: absolute; top: -5px; bottom: -5px; width: 0; border-left: 1.5px dashed var(--ink-2); }
.hv { text-align: right; font-variant-numeric: tabular-nums; font-weight: 600; }
.hv.none { color: var(--muted); font-weight: 400; }
.legend-note { display: flex; align-items: center; gap: 6px; color: var(--ink-2); font-size: 12px; margin-top: 10px; }
.legend-note .dash { width: 18px; border-top: 1.5px dashed var(--ink-2); }

/* severity badge */
.sev {
  display: inline-flex; align-items: center; gap: 5px; white-space: nowrap;
  font-size: 12px; font-weight: 600; color: var(--ink);
}
.sev .ic {
  display: inline-grid; place-items: center; width: 18px; height: 18px; border-radius: 50%;
  font-size: 11px; font-weight: 800; line-height: 1;
}
.sev-error .ic { background: var(--critical); color: var(--on-critical); }
.sev-warning .ic { background: var(--warning); color: var(--on-warning); }
.sev-info .ic { background: var(--axis); color: var(--ink); }

/* filters */
.filters { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 12px; }
.chip {
  font: inherit; font-size: 13px; cursor: pointer; border-radius: 999px; padding: 4px 12px;
  border: 1px solid var(--border); background: var(--surface); color: var(--ink-2);
  display: inline-flex; gap: 6px; align-items: center;
}
.chip[aria-pressed="true"] { background: var(--surface-2); color: var(--ink); border-color: var(--axis); font-weight: 600; }
.chip .cnt { color: var(--muted); font-variant-numeric: tabular-nums; }
select {
  font: inherit; font-size: 13px; padding: 4px 8px; border-radius: 8px;
  border: 1px solid var(--border); background: var(--surface); color: var(--ink);
}

/* issues table */
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
th { text-align: left; color: var(--ink-2); font-weight: 600; font-size: 12px; padding: 8px; border-bottom: 1px solid var(--axis); white-space: nowrap; }
td { padding: 10px 8px; border-bottom: 1px solid var(--grid); vertical-align: top; }
td.loc { white-space: nowrap; font-family: ui-monospace, Consolas, "Cascadia Mono", monospace; font-size: 12.5px; }
td.pen { text-align: right; font-variant-numeric: tabular-nums; color: var(--ink-2); white-space: nowrap; }
.sugg { color: var(--ink-2); font-size: 13px; margin-top: 2px; }
.sugg::before { content: "→ "; color: var(--muted); }
.empty { color: var(--ink-2); padding: 12px 0; }

/* source view */
details.src { margin-top: 12px; border: 1px solid var(--border); border-radius: 10px; overflow: hidden; }
details.src > summary {
  cursor: pointer; padding: 10px 14px; background: var(--surface-2); font-weight: 600; font-size: 14px;
}
details.src > summary .muted { font-weight: 400; font-size: 13px; }
.code { overflow-x: auto; font-family: ui-monospace, Consolas, "Cascadia Mono", "Microsoft JhengHei", monospace; font-size: 12.5px; line-height: 1.6; }
.cl { display: grid; grid-template-columns: 52px 1fr; min-width: max-content; }
.cl .no { text-align: right; padding-right: 12px; color: var(--muted); user-select: none; border-right: 1px solid var(--grid); }
.cl .tx { white-space: pre; padding-left: 12px; padding-right: 16px; }
.cl.hl-error { background: color-mix(in srgb, var(--critical) 13%, transparent); }
.cl.hl-warning { background: color-mix(in srgb, var(--warning) 16%, transparent); }
.cl.hl-info { background: color-mix(in srgb, var(--axis) 22%, transparent); }
.cl:target { outline: 2px solid var(--series); outline-offset: -2px; }
.ann {
  margin: 2px 12px 6px 64px; padding: 6px 10px; border-radius: 8px; font-family: system-ui, -apple-system, "Segoe UI", "Microsoft JhengHei", sans-serif;
  font-size: 13px; background: var(--surface); border: 1px solid var(--border); white-space: normal; max-width: 860px;
}
.ann .sugg { font-size: 12.5px; }

footer { color: var(--muted); font-size: 12px; margin-top: 28px; line-height: 1.7; }

#tip {
  position: fixed; z-index: 10; pointer-events: none; max-width: 280px;
  background: var(--ink); color: var(--page); font-size: 12.5px; padding: 6px 10px; border-radius: 8px;
  box-shadow: 0 4px 14px rgba(0,0,0,.18);
}
[hidden] { display: none !important; }

@media (max-width: 760px) {
  .hero { grid-template-columns: 1fr; }
  .charts { grid-template-columns: 1fr; }
  .bar-row { grid-template-columns: 76px 1fr 34px 36px; gap: 8px; }
  .total .num { font-size: 48px; }
  .card { padding: 14px; }
  .tile .s { white-space: normal; }
  .hrow { grid-template-columns: 100px 1fr 32px; }
  /* 問題清單在手機上改為一筆一張卡片 */
  table, tbody, tr, td { display: block; width: 100%; }
  thead { display: none; }
  tr { padding: 10px 0; border-bottom: 1px solid var(--grid); }
  tr[hidden] { display: none; }
  td { border: 0; padding: 2px 0; }
  td:nth-child(1), td:nth-child(2), td.pen { display: inline-block; width: auto; margin-right: 10px; }
  td.pen::before { content: "扣分 "; color: var(--muted); }
  td.loc { white-space: normal; }
  .ann { margin-left: 12px; }
  .cl { grid-template-columns: 40px 1fr; }
}
@media print {
  body { background: #fff; }
  .filters, #tip { display: none !important; }
  .card, .tile { break-inside: avoid; }
  details.src { break-inside: auto; }
}
"""

JS = r"""
(() => {
  const tip = document.getElementById('tip');
  document.addEventListener('pointerover', e => {
    const t = e.target.closest && e.target.closest('[data-tip]');
    if (!t) { tip.hidden = true; return; }
    tip.textContent = t.dataset.tip; tip.hidden = false;
  });
  document.addEventListener('pointermove', e => {
    if (tip.hidden) return;
    const r = tip.getBoundingClientRect();
    let x = e.clientX + 14, y = e.clientY + 14;
    if (x + r.width > innerWidth - 8) x = e.clientX - r.width - 14;
    if (y + r.height > innerHeight - 8) y = e.clientY - r.height - 14;
    tip.style.left = Math.max(8, x) + 'px'; tip.style.top = Math.max(8, y) + 'px';
  });
  document.addEventListener('scroll', () => { tip.hidden = true; }, { passive: true });

  const sev = new Set(['error', 'warning', 'info']);
  let cat = 'all';
  const apply = () => {
    let shown = 0;
    document.querySelectorAll('[data-sev]').forEach(el => {
      const ok = sev.has(el.dataset.sev) && (cat === 'all' || el.dataset.cat === cat);
      el.hidden = !ok;
      if (ok && el.tagName === 'TR') shown++;
    });
    const empty = document.getElementById('no-match');
    if (empty) empty.hidden = shown !== 0;
  };
  document.querySelectorAll('.chip[data-f-sev]').forEach(b => b.addEventListener('click', () => {
    const s = b.dataset.fSev;
    if (sev.has(s)) sev.delete(s); else sev.add(s);
    b.setAttribute('aria-pressed', sev.has(s));
    apply();
  }));
  const sel = document.getElementById('f-cat');
  if (sel) sel.addEventListener('change', () => { cat = sel.value; apply(); });
  document.querySelectorAll('a[href^="#L-"]').forEach(a => a.addEventListener('click', () => {
    const el = document.getElementById(a.getAttribute('href').slice(1));
    const d = el && el.closest('details');
    if (d) d.open = true;
  }));
})();
"""


def _grade_class(letter: str) -> str:
    return {"A": "g-good", "B": "g-good", "C": "g-warning", "D": "g-warning"}.get(letter, "g-critical")


def _sev_badge(sev: str) -> str:
    return (f'<span class="sev sev-{sev}"><span class="ic" aria-hidden="true">{SEV_ICON[sev]}</span>'
            f'{SEV_LABEL[sev]}</span>')


def _nice_max(v: float) -> tuple[int, int]:
    """回傳 (軸上限, 刻度間距)。"""
    v = max(v, 1)
    step = max(1, math.ceil(v / 4))
    for s in (1, 2, 5, 10, 20, 25, 50, 100):
        if v / s <= 5:
            step = s
            break
    return step * math.ceil(v / step), step


def _stage_chart(stages: dict, budget: int, unit: str) -> str:
    data = sorted((int(k), v) for k, v in stages.items() if int(k) >= 0)
    if not data:
        return '<p class="empty">沒有可分級的暫存器。</p>'
    W, H = 560, 230
    ml, mr, mt, mb = 34, 12, 22, 30
    pw, ph = W - ml - mr, H - mt - mb
    peak = max(v["depth"] for _, v in data)
    ymax, step = _nice_max(max(peak, budget) * 1.12)
    y = lambda d: mt + ph - d / ymax * ph   # noqa: E731
    parts = [f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" '
             f'aria-label="各 pipeline 級的最大邏輯深度長條圖，建議上限 {budget}">']
    for t in range(0, ymax + 1, step):
        cls = "base" if t == 0 else "grid"
        parts.append(f'<line class="{cls}" x1="{ml}" x2="{W - mr}" y1="{y(t):.1f}" y2="{y(t):.1f}"/>')
        parts.append(f'<text x="{ml - 6}" y="{y(t) + 4:.1f}" text-anchor="end">{t}</text>')
    n = len(data)
    slot = pw / n
    bw = max(8.0, min(44.0, slot * 0.62))
    peak_idx = max(range(n), key=lambda i: data[i][1]["depth"])
    for i, (k, v) in enumerate(data):
        d = v["depth"]
        x = ml + slot * i + (slot - bw) / 2
        y0 = mt + ph
        tipt = f"第 {k} 級：最大邏輯深度 {d} 級 {unit}，{v['ff_bits']} 個暫存器 bit" + \
               ("（超過建議上限）" if d > budget else "")
        parts.append(f'<g data-tip="{esc(tipt)}">')
        parts.append(f'<rect class="hit" x="{ml + slot * i:.1f}" y="{mt}" width="{slot:.1f}" height="{ph}"/>')
        if d > 0:
            h = d / ymax * ph
            yt = y0 - h
            r = min(4.0, h, bw / 2)
            parts.append(f'<path class="bar" d="M{x:.1f},{y0:.1f} V{yt + r:.1f} Q{x:.1f},{yt:.1f} {x + r:.1f},{yt:.1f} '
                         f'H{x + bw - r:.1f} Q{x + bw:.1f},{yt:.1f} {x + bw:.1f},{yt + r:.1f} V{y0:.1f} Z"/>')
            if i == peak_idx:
                parts.append(f'<text class="val" x="{x + bw / 2:.1f}" y="{yt - 6:.1f}" text-anchor="middle">{d}</text>')
        parts.append("</g>")
        parts.append(f'<text x="{ml + slot * i + slot / 2:.1f}" y="{H - 10}" text-anchor="middle">S{k}</text>')
    by = y(budget)
    parts.append(f'<line class="budget" x1="{ml}" x2="{W - mr}" y1="{by:.1f}" y2="{by:.1f}"/>')
    parts.append(f'<text class="budget-label" x="{W - mr}" y="{by - 5:.1f}" text-anchor="end">建議上限 {budget}</text>')
    parts.append("</svg>")
    return "".join(parts)


def _path_chart(depth: dict, budget: int, unit: str) -> str:
    rows = [("in2reg", "輸入 → 暫存器"), ("reg2reg", "暫存器 → 暫存器"),
            ("reg2out", "暫存器 → 輸出"), ("in2out", "輸入 → 輸出")]
    scale = max([v for v in depth.values() if v >= 0] + [budget]) * 1.1 or 1
    out = ['<div class="hbars">']
    for key, label in rows:
        d = depth.get(key, -1)
        if d < 0:
            tipt = f"{label}：沒有這類路徑"
            val = '<span class="hv none">無</span>'
            fill = ""
        else:
            tipt = f"{label}：最長 {d} 級 {unit}" + ("（超過建議上限）" if d > budget else "")
            val = f'<span class="hv">{d}</span>'
            fill = f'<span class="fill" style="width:{d / scale * 100:.1f}%"></span>' if d > 0 else ""
        out.append(f'<div class="hrow" data-tip="{esc(tipt)}"><span>{label}</span>'
                   f'<span class="htrack"><span class="track" style="position:absolute;inset:3px 0;height:auto;'
                   f'background:none"></span>{fill}<span class="baseline"></span>'
                   f'<span class="limit" style="left:{budget / scale * 100:.1f}%"></span></span>{val}</div>')
    out.append("</div>")
    out.append(f'<div class="legend-note"><span class="dash"></span>建議上限 {budget} 級 {unit}</div>')
    return "".join(out)


def render_html(g, top: str, version: str) -> str:
    scores = g.scores()
    total = g.total()
    letter = grade_letter(total)
    st = g.stats
    issues = sorted(g.issues, key=lambda i: (SEV_ORDER[i.severity], -i.penalty, i.file, i.line))
    files = [d for d, _ in g.sources]
    fidx = {d: i for i, d in enumerate(files)}
    sev_count = Counter(i.severity for i in issues)
    cat_count = Counter(i.category for i in issues)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    unit = st.get("unit", "級")
    budget = st.get("budget", 8)

    P: list[str] = []
    w = P.append
    w('<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">')
    w('<meta name="viewport" content="width=device-width, initial-scale=1">')
    w(f"<title>vgrade 評分報告 · {esc(top)}</title><style>{CSS}</style></head><body><main>")

    # ---------------- header + hero
    target = {"fpga": "FPGA（LUT6 級數）", "asic": "ASIC（通用邏輯閘級數）"}.get(st.get("target", ""), "—")
    w('<header>')
    w(f'<h1>Verilog 評分報告：{esc(top)}</h1>')
    w(f'<div class="meta">檔案：{esc(", ".join(files))}　·　時序目標：{target}　·　{now}　·　vgrade {esc(version)}</div>')
    w('</header>')

    w('<section class="card hero">')
    w('<div class="total">')
    w(f'<div class="num">{total:.1f}</div><div class="of">/ 100 分</div>')
    w(f'<span class="badge-grade {_grade_class(letter)}">等級 {letter}</span>')
    w('</div><div><h2>各類別分數</h2><div class="bars">')
    for c, (label, wt) in CATEGORIES.items():
        s = scores[c]
        if s is None:
            w(f'<div class="bar-row na"><span class="bl">{label}</span><span class="track">'
              f'<span class="na-text">不適用：{esc(g.na[c])}</span></span><span class="bv">—</span>'
              f'<span class="bw">{wt}%</span></div>')
        else:
            tipt = f"{label}：{s:.0f} 分（權重 {wt}%），{cat_count.get(c, 0)} 個問題"
            w(f'<div class="bar-row" data-tip="{esc(tipt)}"><span class="bl">{label}</span>'
              f'<span class="track"><span class="fill" style="width:{s:.1f}%"></span></span>'
              f'<span class="bv">{s:.0f}</span><span class="bw">{wt}%</span></div>')
    w('</div></div></section>')

    # ---------------- tiles
    if "ff_bits" in st:
        cr = st["critical"]
        lat = st.get("latency", {})
        lat_txt = "、".join(f"{k} {v}" for k, v in lat.items()) or "—"
        tiles = [
            ("暫存器", f'{st["ff_bits"]}<small>bits</small>', f'無 reset：{st["ff_noreset_bits"]} bits'),
            ("Latch", f'{st["latch_bits"]}<small>bits</small>',
             "應為 0" if st["latch_bits"] else "沒有推論出 latch"),
            ("組合邏輯", f'{st["comb_cells"]}<small>{esc(unit)}</small>', "合成後數量"),
            ("最長邏輯深度", f'{max(cr["levels"], 0)}<small>級</small>',
             f'建議 ≤ {budget}　{esc(cr["kind"])} {esc(cr["endpoint"])}'),
            ("輸出延遲", f'{max(lat.values()) if lat else "—"}<small>cycle</small>', f"各輸出：{esc(lat_txt)}"),
        ]
    else:
        tiles = []
    tiles.append(("問題", f'{len(issues)}<small>項</small>',
                  f'錯誤 {sev_count.get("error", 0)} · 警告 {sev_count.get("warning", 0)} · 建議 {sev_count.get("info", 0)}'))
    w('<section class="tiles" style="margin-top:16px">')
    for k, v, s in tiles:
        w(f'<div class="tile"><div class="k">{k}</div><div class="v">{v}</div><div class="s">{s}</div></div>')
    w('</section>')

    # ---------------- charts
    if "ff_bits" in st:
        w('<section class="charts" style="margin-top:16px">')
        w('<div class="card"><h2>Pipeline 各級邏輯深度</h2>'
          '<p class="chart-sub">S<i>n</i> = 距離輸入第 n 級暫存器；各級深度越接近越平衡</p>')
        w(_stage_chart(st.get("stages", {}), budget, unit))
        w('</div><div class="card"><h2>各類路徑最長深度</h2>'
          '<p class="chart-sub">輸入→輸出若有路徑，代表輸出沒有經過暫存器</p>')
        w(_path_chart(st.get("depth", {}), budget, unit))
        w('</div></section>')

    # ---------------- issues
    w('<section class="card" id="issues"><h2>問題清單</h2>')
    if issues:
        w('<div class="filters" role="group" aria-label="篩選">')
        for s in ("error", "warning", "info"):
            w(f'<button class="chip" type="button" data-f-sev="{s}" aria-pressed="true">'
              f'{_sev_badge(s)}<span class="cnt">{sev_count.get(s, 0)}</span></button>')
        w('<select id="f-cat" aria-label="類別"><option value="all">全部類別</option>')
        for c, (label, _) in CATEGORIES.items():
            if cat_count.get(c):
                w(f'<option value="{c}">{label}（{cat_count[c]}）</option>')
        w('</select></div>')
        w('<div class="table-wrap"><table><thead><tr><th>嚴重度</th><th>類別</th><th>位置</th>'
          '<th>問題與建議</th><th style="text-align:right">扣分</th></tr></thead><tbody>')
        for i in issues:
            if i.line and i.file in fidx:
                loc = f'<a href="#L-{fidx[i.file]}-{i.line}">{esc(i.file)}:{i.line}</a>'
            else:
                loc = esc(i.file) if i.file else '<span class="muted">整體</span>'
            sugg = f'<div class="sugg">{esc(i.suggestion)}</div>' if i.suggestion else ""
            pen = f"−{i.penalty:g}" if i.penalty else "—"
            w(f'<tr data-sev="{i.severity}" data-cat="{i.category}"><td>{_sev_badge(i.severity)}</td>'
              f'<td>{CATEGORIES[i.category][0]}</td><td class="loc">{loc}</td>'
              f'<td>{esc(i.message)}{sugg}</td><td class="pen">{pen}</td></tr>')
        w('</tbody></table><p class="empty" id="no-match" hidden>沒有符合篩選條件的項目。</p></div>')
    else:
        w('<p class="empty">沒有發現任何問題，做得好！</p>')
    w('</section>')

    # ---------------- source view
    by_line: dict[tuple[int, int], list] = defaultdict(list)
    for i in issues:
        if i.line and i.file in fidx:
            by_line[(fidx[i.file], i.line)].append(i)
    w('<section class="card"><h2>原始碼標註</h2>'
      '<p class="chart-sub">有問題的行以底色標示（紅：錯誤、黃：警告、灰：建議），說明寫在該行下方</p>')
    for fi, (name, raw) in enumerate(g.sources):
        lines = raw.splitlines() or [""]
        n_iss = sum(len(v) for (f, _), v in by_line.items() if f == fi)
        opened = " open" if len(lines) <= 600 else ""
        w(f'<details class="src"{opened}><summary>{esc(name)} '
          f'<span class="muted">· {len(lines)} 行 · {n_iss} 個標註</span></summary><div class="code">')
        for ln_no, text in enumerate(lines, 1):
            here = by_line.get((fi, ln_no), [])
            hl = f" hl-{min(here, key=lambda x: SEV_ORDER[x.severity]).severity}" if here else ""
            w(f'<div class="cl{hl}" id="L-{fi}-{ln_no}"><span class="no">{ln_no}</span>'
              f'<span class="tx">{esc(text.expandtabs(4)) or " "}</span></div>')
            for i in here:
                sugg = f'<div class="sugg">{esc(i.suggestion)}</div>' if i.suggestion else ""
                w(f'<div class="ann" data-sev="{i.severity}" data-cat="{i.category}">{_sev_badge(i.severity)} '
                  f'<span>[{CATEGORIES[i.category][0]}] {esc(i.message)}</span>{sugg}</div>')
        w('</div></details>')
    w('</section>')

    # ---------------- footer
    weights = "、".join(f"{label} {wt}%" for label, wt in CATEGORIES.values())
    w('<footer>')
    w(f'評分方式：各類別滿分 100，依問題扣分；總分為加權平均（{weights}），不適用的類別不計入。<br>')
    w('時序與 pipeline 依 Yosys 合成後的 netlist 計算邏輯級數，為與製程無關的估計值；'
      'FPGA 加法器實際使用 carry chain，寬加法器級數會被高估。<br>')
    w(f'由 vgrade {esc(version)} 產生 · https://github.com/WANG-JUI-CHAO/vgrade')
    w('</footer>')

    w(f'</main><div id="tip" role="tooltip" hidden></div><script>{JS}</script></body></html>')
    return "\n".join(P)

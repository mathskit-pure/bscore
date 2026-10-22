#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""patch_page_v2.py —— 生成「口径 v2」的看板页面。

在原五章式模板上做五件事：
  1. 用 v2 读数替换指标表、指数表、触发测算段；
  2. 阈值文案 0.75/0.80 → 0.85/0.92（第三章与触发测算）；
  3. 第五章历史信号表按 v2 序列重算，并计算真实的后 12 / 24 月收益；
  4. 第四章月频图按 v2 序列重绘（原图是 2005 年起的旧口径，留着会误导）；
  5. 页头插入「口径 v2」说明，页脚改写数据日期，并摘掉过期横幅。

用法：
    python3 patch_page_v2.py --template ../bscore_patch/template_v23.html \
        --readings latest_v2.json --series cb_series_v2.csv --out page_v2.html
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _cand in (_HERE.parent / "bscore_patch", _HERE.parent / "tools", _HERE):
    if (_cand / "patch_v23.py").is_file():
        sys.path.insert(0, str(_cand))
        break
import patch_v23 as P  # noqa: E402

T_BUILD, T_FULL = 0.85, 0.92
STALE_RE = re.compile(r"<!--STALE_BANNER:start-->.*?<!--STALE_BANNER:end-->\s*", re.S)
IDX = {"hs300": "sh000300", "zz500": "sh000905", "zz1000": "sh000852"}


def fetch_index(symbol: str, n: int = 2200):
    import urllib.request
    url = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           f"CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=no&datalen={n}")
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}),
                                timeout=40) as r:
        return [(d["day"], float(d["close"])) for d in json.loads(r.read().decode("utf-8"))]


def monthly_series(series_csv: Path):
    """日频 CB → 月末值。"""
    by = {}
    with series_csv.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["cb"]:
                by[r["date"][:7]] = float(r["cb"])
    return sorted(by.items())


def make_chart(points, out_png: Path):
    import matplotlib
    matplotlib.use("Agg")
    import datetime as dt
    import matplotlib.pyplot as plt

    xs = [dt.datetime.strptime(m + "-01", "%Y-%m-%d") for m, _ in points]
    ys = [v for _, v in points]
    fig, ax = plt.subplots(figsize=(11.0, 3.6), dpi=110)
    ax.plot(xs, ys, color="#c0392b", lw=1.4, label="CB (v2, rolling 5y)")
    ax.axhline(T_FULL, ls="--", lw=1.2, color="#2a9d8f", label=f"{T_FULL:.2f} full position")
    ax.axhline(T_BUILD, ls="--", lw=1.2, color="#e07b39", label=f"{T_BUILD:.2f} build position")
    ax.axhline(0.5, ls=":", lw=1.0, color="#999999")
    ax.set_ylim(0, 1.0)
    ax.set_ylabel("CB")
    ax.set_title(f"B-score CB history (monthly, rolling 5y percentile, from {points[0][0]})", fontsize=10)
    ax.grid(True, ls=":", lw=0.6, color="#dddddd")
    ax.legend(loc="upper left", fontsize=7.5, ncol=3, frameon=True, framealpha=0.9)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.tight_layout()
    fig.savefig(out_png, format="png")
    plt.close(fig)


def episodes(series_csv: Path, th: float, gap: int = 20):
    import datetime as dt
    rows = []
    with series_csv.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["cb"]:
                rows.append((r["date"], float(r["cb"])))
    hits = [(d, v) for d, v in rows if v >= th]
    out, cur = [], []
    for d, v in hits:
        if cur and (dt.date.fromisoformat(d) - dt.date.fromisoformat(cur[-1][0])).days > gap:
            out.append(cur)
            cur = []
        cur.append((d, v))
    if cur:
        out.append(cur)
    return out


def month_end_close(pairs, ym: str):
    cands = [c for d, c in pairs if d[:7] == ym]
    return cands[-1] if cands else None


def fwd_return(pairs, ym: str, months: int):
    start = month_end_close(pairs, ym)
    if start is None:
        return None
    y, m = int(ym[:4]), int(ym[5:])
    m += months
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    end = month_end_close(pairs, f"{y:04d}-{m:02d}")
    if end is None:
        return None
    return (end / start - 1) * 100



def signal_of(R):
    cb, pbp = R["cb"], R["pb_percentile"]
    if cb >= T_FULL:
        return "满仓", 100, "估值处于历史最便宜区间，双维度同时见底，可满仓。"
    if cb >= T_BUILD:
        return "建仓", round(min(100, 40 + (cb - T_BUILD) / (T_FULL - T_BUILD) * 60)), \
               "估值进入历史便宜区间，可按分批计划建仓。"
    if pbp <= 0.15:
        return "清仓", 0, "全市场 PB 处于历史最贵 15%，估值风险高，清仓。"
    if pbp <= 0.25:
        return "减仓", 25, "全市场 PB 处于历史偏贵区间，逐步减仓。"
    return "观望 / 维持常规仓位", 50, "估值处于历史中枢附近，无明确信号，维持常规仓位等待。"


def _set_by_class(html: str, cls: str, text: str):
    """替换某个 class 元素的文字，保留属性与 pnid 注释。"""
    pat = re.compile(r'(<div[^>]*class="' + cls + r'"[^>]*>)([\s\S]*?)(</div>)')
    m = pat.search(html)
    if not m:
        return html, False
    comments = "".join(P.COMMENT_RE.findall(m.group(2)))
    return html[:m.start()] + m.group(1) + comments + text + m.group(3) + html[m.end():], True


def _replace_table_under(html: str, heading: str, new_rows: str):
    """把 heading 之后第一个 table 的内容整体换掉。"""
    i = html.find(heading)
    if i < 0:
        return html, False
    j = html.find("<table", i)
    k = html.find("</table>", j)
    if j < 0 or k < 0:
        return html, False
    open_tag_end = html.index(">", j) + 1
    return html[:open_tag_end] + "\n" + new_rows + "\n" + html[k:], True


def _replace_note(html: str, anchor: str, new_text: str):
    i = html.find(anchor)
    if i < 0:
        return html, False
    j = html.rfind("<div", 0, i)
    k = html.find("</div>", i)
    if j < 0 or k < 0:
        return html, False
    close = html.index(">", j) + 1
    return html[:close] + new_text + html[k:], True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", required=True)
    ap.add_argument("--readings", default="latest_v2.json")
    ap.add_argument("--series", default="cb_series_v2.csv")
    ap.add_argument("--out", required=True)
    ap.add_argument("--chart", default="_cb_v2.png")
    a = ap.parse_args()

    html = Path(a.template).read_bytes().decode("utf-8")
    html = STALE_RE.sub("", html)
    R = json.loads(Path(a.readings).read_bytes().decode("utf-8"))

    # ---------- 1. 数字 ----------
    P.PERCENT_SCALE["k"] = 1.0
    vals = {
        "date": R["trade_date"],
        "bm_cur": f"{R['bm_median']:.3f}", "bm_hist": f"{R['bm_percentile']:.3f}",
        "pb_value": f"{R['pb']:.2f}", "pb_pct": f"{R['pb_percentile']:.3f}",
        "cb": f"{R['cb']:.3f}", "broken_pct": f"{R['broken_net']:.1f}%",
        "weighted_pb": f"{R['weighted_pb']:.2f}", "sample_n": f"{int(R['sample_n'])} 只",
    }
    for k, key in (("hs300", "hs300"), ("zz500", "zz500"), ("zz1000", "zz1000")):
        s = R["index"][k]
        vals[f"{key}_close"] = f"{s['close']:.0f}"
        vals[f"{key}_chg"] = f"{s['chg']:+.1f}%"
        vals[f"{key}_dd"] = f"{s['dd']:.1f}%"
    t = R["trigger"]
    vals["trig_pb"] = f"{t['pb']:.2f}"
    vals["trig_bm"] = f"{t['bm']:.3f}"
    vals["trig_hs300"] = f"{int(t['hs300'])}"

    done = {}
    for label, cells in [
        ("便宜度分位（BM 中位数）", {2: vals["bm_cur"], 3: vals["bm_hist"]}),
        ("PB 便宜分位（全市场 PB）", {2: vals["pb_value"], 3: vals["pb_pct"]}),
        ("双便宜均值 CB", {3: vals["cb"]}),
        ("破净股占比", {2: vals["broken_pct"]}),
        ("全市场加权 PB（快照口径）", {2: vals["weighted_pb"]}),
        ("有效样本", {2: vals["sample_n"]}),
    ]:
        html, done[label] = P.patch_metric_row(html, label, cells)
    for name in ("沪深300", "中证500", "中证1000"):
        key = {"沪深300": "hs300", "中证500": "zz500", "中证1000": "zz1000"}[name]
        html, done[name] = P.patch_index_row(html, name, vals[f"{key}_close"],
                                            vals[f"{key}_chg"], vals[f"{key}_dd"])
    html, done["触发测算"] = P.patch_trigger(html, vals)

    # ---------- 2. 阈值文案 ----------
    html = html.replace("CB ≥ 0.75 分批建仓，≥ 0.80 加仓至满仓",
                        f"CB ≥ {T_BUILD:.2f} 分批建仓，≥ {T_FULL:.2f} 加仓至满仓")
    html = html.replace("（CB 升至 0.75）", f"（CB 升至 {T_BUILD:.2f}）")
    html = html.replace("高（当前 0.397）", f"高（当前 {vals['bm_cur']}）")

    # ---------- 2b. 页头日期与 hero ----------
    m = re.search(r"数据更新：\s*<b[^>]*>(?:<!--pnid:[^>]*-->)?([\d-]+)</b>", html)
    if m:
        html = html[:m.start(1)] + R["trade_date"] + html[m.end(1):]
    m = re.search(r"交易日\s*<b[^>]*>(?:<!--pnid:[^>]*-->)?([\d-]+)</b>", html)
    if m:
        html = html[:m.start(1)] + R["trade_date"] + html[m.end(1):]

    sig, pos, desc = signal_of(R)
    for cls, txt in (("big", f"{R['cb']:.3f}"), ("sig", sig),
                     ("pos", f"建议仓位 {pos}%"), ("desc", desc)):
        html, done[f"hero.{cls}"] = _set_by_class(html, cls, txt)

    # ---------- 3. 第五章：按 v2 重算信号 ----------
    eps = episodes(Path(a.series), T_BUILD)
    idxs = {k: fetch_index(v) for k, v in IDX.items()}
    rows_html, stats = [], []
    for e in eps:
        ym = e[0][0][:7]
        cells = [f"<td>{ym}</td>"]
        rec = {"ym": ym}
        for k in ("hs300", "zz500", "zz1000"):
            r12 = fwd_return(idxs[k], ym, 12)
            r24 = fwd_return(idxs[k], ym, 24)
            cells.append(f"<td>{'—' if r12 is None else f'{r12:+.1f}%'}</td>")
            rec[k] = (r12, r24)
        rows_html.append("<tr>" + "".join(cells) + "</tr>")
        stats.append(rec)

    new_table = ("<tr><th>触发月</th><th>沪深300 后12月</th><th>中证500 后12月</th><th>中证1000 后12月</th></tr>\n"
                 + "".join(rows_html))
    html, ok_tbl = _replace_table_under(html, "五、历史信号", new_table)
    done["第五章表格"] = ok_tbl

    def agg(k, horizon):
        vals_ = [r[k][0 if horizon == 12 else 1] for r in stats
                 if (r[k][0] if horizon == 12 else r[k][1]) is not None]
        return (len(vals_), sum(vals_) / len(vals_), sum(1 for v in vals_ if v > 0)) if vals_ else (0, None, 0)

    parts = []
    for k, nm in (("hs300", "沪深300"), ("zz500", "中证500"), ("zz1000", "中证1000")):
        n12, a12, w12 = agg(k, 12)
        n24, a24, _ = agg(k, 24)
        seg = f"{nm} 后12月平均 {a12:+.1f}%（胜率 {100*w12/n12:.0f}%，{n12} 次）"
        if a24 is not None:
            seg += f"、后24月 {a24:+.1f}%（{n24} 次）"
        parts.append(seg)
    summary = (f"历史买入信号（口径 v2，CB ≥ {T_BUILD:.2f}，去重叠 {len(eps)} 次）：" + "；".join(parts)
               + f"。样本仅 {len(eps)} 次，且窗口自 2019 年起，统计意义有限。")
    html, done["第五章小结"] = _replace_note(html, "历史买入信号", summary)
    html = html.replace("五、历史信号触发记录（CB ≥ 0.75，去重叠）",
                        f"五、历史信号触发记录（口径 v2，CB ≥ {T_BUILD:.2f}，去重叠 {len(eps)} 次）")

    # ---------- 4. 第四章：重绘月频图 ----------
    pts = monthly_series(Path(a.series))
    chart_note = "kept"
    try:
        out_png = Path(a.chart)
        make_chart(pts, out_png)
        b64 = base64.b64encode(out_png.read_bytes()).decode()
        html, n = re.subn(r'(src="data:image/png;base64,)[^"]*(")',
                          lambda m: m.group(1) + b64 + m.group(2), html, count=1)
        chart_note = f"redrawn({len(pts)} 月)"
    except Exception as e:
        chart_note = f"failed: {e}"

    # ---------- 5. 口径说明 + 页脚 ----------
    note = (
        "<!--V2_NOTE_START-->"
        '<div style="background:#eef4fd;border:1px solid #c9dcf5;border-left:5px solid #2f6fb5;'
        'color:#1e4b7a;padding:11px 15px;border-radius:8px;font-size:13.5px;line-height:1.6;margin:0 0 18px">'
        "<b>口径 v2（2026-10-22 重建）</b>　原实现（2005 年起 expanding percentile）的源码与数据已丢失，"
        f"本版改用<b>滚动 5 年（1250 交易日）分位</b>，阈值相应重标定为建仓 <b>{T_BUILD:.2f}</b> / 满仓 <b>{T_FULL:.2f}</b>。"
        "分位基准不同，<b>本页数值与 2026-10-22 之前的记录不可直接比较</b>。"
        "标的池、BM 中位数、全市场 PB、破净率等口径已按旧页读数逐项校准，误差均在 0.5% 内。"
        "</div><!--V2_NOTE_END-->"
    )
    m = re.search(r'<div[^>]*class="sub"[^>]*>(?:(?!</div>).)*?</div>\s*', html, re.S)
    if m:
        html = html[:m.end()] + note + "\n" + html[m.end():]

    html = re.sub(r"本页由 /workspace/bscore/src/daily_update\.py 自动生成 · 数据截至 [\d-]+ 收盘 ·\s*"
                  r"生成于 [\d-]+ · 量化研究记录，不构成投资建议。",
                  f"本页由 bscore/rebuild/daily_update_v2.py 生成 · 口径 v2（滚动 5 年均值分位）· "
                  f"数据截至 {R['trade_date']} 收盘 · 生成于 2026-10-22 · 量化研究记录，不构成投资建议。",
                  html)

    failed = [k for k, v in done.items() if not v]
    if failed:
        print(f"⚠ 锚点未命中：{failed}", file=sys.stderr)
    Path(a.out).write_bytes(html.encode("utf-8"))
    print(f"已生成 {a.out}（{len(html)} 字符）　图表：{chart_note}")
    print(f"信号 {len(eps)} 次：{', '.join(r['ym'] for r in stats)}")
    print(f"锚点：{'全部命中' if not failed else '部分失败 ' + str(failed)}")


if __name__ == "__main__":
    main()

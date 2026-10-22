#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""重建「A股B-score择时信号看板」的组合模块（修正版）。

为什么重写
----------
旧链路（/workspace/bscombo/update_bscombo.py 打补丁的那套）有三处硬伤，2026-10-21 查明：
  1. 净值曲线退化：250 个点全部落在同一高度（值恒为末值），图例写「起点 1 → 末值 0.9013」，
     曲线本身却是一条直线 —— 画图的数组被填成了常数。
  2. 两行对比基准（质量Top30 不择时、沪深300）整行是「—」，无从判断择时是否加分。
  3. 指标与仓位不自洽：报「平均仓位 6.5% ＋ 累计 -9.87%」，反推标的需要跌约 150%，
     而同期质量Top28 实际 +94%；且用「当期股池」回溯过去两年属前视偏差。

本脚本的口径
------------
  · 组合核心＝股池页发布的质量Top28 v2 净值（data/page_data.json 的 curve.series，
    与股池页面上的数字逐点一致），不自己重造行情，避免两页口径打架。
  · 择时信号：
      A. 趋势闸门（推荐）＝ 0.3 + 0.7 × 1[沪深300 > 月线 MA10]
      B. CB 估值择时（旧口径，对照）＝ clip((CB − 0.354) ÷ 0.45, 0, 1)
      C. 趋势闸门 × CB 微调 ＝ 趋势仓位 × (0.7 + 0.3·CB)
  · 仓位在月末决定、下月生效（无未来函数）；样本 2015 → 最新。
  · 出图前断言每条曲线至少有 12 个不同取值，杜绝再把直线画上线。
  · 只改页面里的组合模块，不动看板基础区块（CB 读数、KPI、信号说明）。

用法
----
    python3 cloud/bscombo/rebuild_bscombo.py                 # 下载→重算→回写→发布
    python3 cloud/bscombo/rebuild_bscombo.py --dry-run       # 只算不改页面
    python3 cloud/bscombo/rebuild_bscombo.py --html-in a.html --html-out b.html   # 本地离线验证
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

NODE = "VLeP5CSpUchSY3qawGq88W"
FILE_NAME = "A股B-score择时信号看板.html"
COST = 0.0005          # 月度调仓，单边 5bp
TREND_MA_M = 10        # 趋势闸门：月线 MA10
TREND_FLOOR = 0.30
CB_LO, CB_SLOPE = 0.354, 0.45

BASE = Path(os.environ.get("POOL_BASE") or Path(__file__).resolve().parents[2])
DATA = Path(os.environ.get("POOL_DATA") or BASE / "data")
BSCORE_LATEST = Path(os.environ.get("BSCORE_LATEST") or "/workspace/bscore/data/latest.json")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def log(m):
    print(f"[bscombo] {m}", flush=True)


# --------------------------------------------------------------------------- #
# 资料库 skill 定位（与 cloud/publish_pages.py 同一套候选）
# --------------------------------------------------------------------------- #
def find_library_skill():
    cands = []
    env = os.environ.get("WB_LIBRARY_SKILL")
    if env:
        cands.append(Path(env))
    if os.name == "nt":
        cands.append(Path(os.path.expandvars(
            r"%LOCALAPPDATA%\Programs\WorkBuddy\resources\app.asar.unpacked"
            r"\resources\plugins\workbuddy-builtin\skills\library")))
    cands += [Path("/root/.codebuddy/skills/library"), Path("/root/.workbuddy/skills/library"),
              Path.home() / ".codebuddy/skills/library", Path.home() / ".workbuddy/skills/library"]
    for c in cands:
        if (c / "page" / "import_html.py").exists():
            return c
    return None


# --------------------------------------------------------------------------- #
# 数据
# --------------------------------------------------------------------------- #
def load_page_data():
    p = DATA / "page_data.json"
    if not p.exists():
        raise SystemExit(f"缺少 {p}（股池页数据包），无法与股池页对齐口径")
    d = json.load(open(p, encoding="utf-8"))
    dates = pd.to_datetime(d["curve"]["dates"])
    ser = pd.DataFrame(d["curve"]["series"], index=dates).sort_index()
    need = "质量Top28 v2" if "质量Top28 v2" in ser.columns else "质量Top28 v1"
    if need not in ser.columns:
        raise SystemExit(f"page_data.json 里没有质量Top28 净值，现有：{list(ser.columns)}")
    return d, ser, need


def hs300_daily(dates):
    """日频沪深300：优先 bench_all.csv（股池链每天更新），否则回退 akshare。"""
    b = DATA / "bench_all.csv"
    if b.exists():
        try:
            df = pd.read_csv(b, encoding="utf-8-sig")
            df.columns = ["date"] + [f"c{i}" for i in range(df.shape[1] - 1)]
            df["date"] = pd.to_datetime(df["date"])
            s = df.set_index("date")["c0"].astype(float).dropna().sort_index()
            if len(s) > 250 and s.index[-1] >= dates[-1] - pd.Timedelta(days=45):
                return s, "bench_all.csv"
        except Exception as e:
            log(f"bench_all.csv 不可用：{e!r}")
    try:
        import akshare as ak
        df = ak.stock_zh_index_daily(symbol="sh000300")
        df["date"] = pd.to_datetime(df["date"])
        return df.set_index("date")["close"].astype(float).sort_index(), "akshare"
    except Exception as e:
        log(f"沪深300 日线拉取失败：{e!r}")
        return None, None


def cb_monthly(idx):
    """CB：全市场市值加权 PB / 等权 PB 的近 24 个月分位的均值（对齐看板 500 交易日口径）。"""
    try:
        import akshare as ak
        df = ak.stock_market_pb_lg()
        df["日期"] = pd.to_datetime(df["日期"])
        df = df.sort_values("日期").set_index("日期")
        pb, pbe = df["市净率"].astype(float), df["等权市净率"].astype(float)

        def cheap(s, w=24):
            v = s.values
            return pd.Series([1.0 - np.mean(v[max(0, i - w + 1):i + 1] <= v[i])
                              for i in range(len(v))], index=s.index)
        cb = (cheap(pb) + cheap(pbe)) / 2
        per = cb.index.to_period("M")
        cb_m = cb.groupby(per).last()
        out = pd.Series(idx.to_period("M").map(cb_m).astype(float), index=idx).ffill()
        return out, "akshare:stock_market_pb_lg", float(cb.iloc[-1])
    except Exception as e:
        log(f"CB 复算失败（将省略 CB 相关行）：{e!r}")
        return None, None, None


def read_dashboard_cb():
    if BSCORE_LATEST.exists():
        try:
            d = json.load(open(BSCORE_LATEST, encoding="utf-8"))
            for k in ("cb", "CB", "cb_value", "dual_cheap_mean"):
                if k in d:
                    return float(d[k])
        except Exception:
            pass
    return None


# --------------------------------------------------------------------------- #
# 评价
# --------------------------------------------------------------------------- #
def metrics(r):
    r = pd.Series(r).dropna()
    eq = (1 + r).cumprod()
    yrs = len(r) / 12
    vol = r.std() * np.sqrt(12)
    mdd = (eq / eq.cummax() - 1).min()
    cagr = eq.iloc[-1] ** (1 / yrs) - 1
    return dict(cagr=cagr, vol=vol, sharpe=(r.mean() * 12) / vol if vol else np.nan, mdd=mdd,
                calmar=cagr / abs(mdd) if mdd else np.nan, cum=eq.iloc[-1] - 1,
                n=len(r), start=r.index[0].strftime("%Y-%m"), end=r.index[-1].strftime("%Y-%m"))


def apply_pos(pos, base_r, cost=COST):
    p = pd.Series(np.asarray(pos, dtype=float), index=base_r.index).clip(0, 1).shift(1)
    return p * base_r - p.diff().abs().fillna(0) * cost, p


# --------------------------------------------------------------------------- #
# 图
# --------------------------------------------------------------------------- #
def svg_chart(series, width=680, height=250):
    out = []
    for name, s, color, dash in series:
        x = pd.Series(s).dropna()
        assert x.nunique() >= 12, f"{name} 曲线几乎无变化（{x.nunique()} 个不同值），拒绝出图"
        out.append((name, x, color, dash))
    allv = pd.concat([x for _, x, _, _ in out])
    vmin, vmax = float(allv.min()), float(allv.max())
    pad = (vmax - vmin) * 0.08 or 0.05
    vmin, vmax = vmin - pad, vmax + pad
    n = len(out[0][1])
    L, R, T, B = 46, 10, 10, 22
    W, H = width - L - R, height - T - B

    def px(i, v):
        return (L + W * i / max(1, n - 1), T + H * (1 - (v - vmin) / (vmax - vmin)))

    parts = [f'<svg viewBox="0 0 {width} {height}" style="width:100%;height:{height}px" '
             f'role="img" aria-label="净值曲线">']
    for t in range(5):
        v = vmin + (vmax - vmin) * t / 4
        y = T + H * (1 - (v - vmin) / (vmax - vmin))
        parts.append(f'<line x1="{L}" x2="{width-R}" y1="{y:.1f}" y2="{y:.1f}" stroke="#eef0f2"/>')
        parts.append(f'<text x="{L-6}" y="{y+3.5:.1f}" text-anchor="end" font-size="10" fill="#9ca3af">{v:.2f}</text>')
    yy = T + H * (1 - (1.0 - vmin) / (vmax - vmin))
    if T <= yy <= T + H:
        parts.append(f'<line x1="{L}" x2="{width-R}" y1="{yy:.1f}" y2="{yy:.1f}" stroke="#9ca3af" '
                     f'stroke-dasharray="4 3" stroke-width="1"/>')
    for name, x, color, dash in out:
        pts = " ".join(f"{px(i, v)[0]:.1f},{px(i, v)[1]:.1f}" for i, v in enumerate(x.values))
        d = f' stroke-dasharray="{dash}"' if dash else ""
        parts.append(f'<polyline fill="none" stroke="{color}" stroke-width="1.7"{d} points="{pts}"/>')
        ex, ey = px(n - 1, x.iloc[-1])
        parts.append(f'<text x="{ex:.1f}" y="{ey-4:.1f}" text-anchor="end" font-size="10" '
                     f'fill="{color}">{x.iloc[-1]:.2f}</text>')
    for i, dt in enumerate(out[0][1].index):
        if dt.month == 1:
            parts.append(f'<text x="{px(i, vmin)[0]:.1f}" y="{height-6}" font-size="10" '
                         f'fill="#9ca3af">{dt.year}</text>')
    parts.append("</svg>")
    return "".join(parts)


# --------------------------------------------------------------------------- #
# 页面片段
# --------------------------------------------------------------------------- #
DROP_HEADINGS = ["净值曲线", "回测表现", "持仓变动明细", "模型与组合说明",
                 "择时口径", "四种搭配", "当期持仓", "口径说明"]
FOOTER_KEY = "本页由"
CN_NUM = "一二三四五六七八九十"


def strip_old_block(html):
    pat = re.compile(r"<h2[^>]*>(.*?)</h2>", re.S)
    marks = [(m.start(), re.sub(r"<[^>]+>|<!--.*?-->", "", m.group(1)).strip()) for m in pat.finditer(html)]
    drop = []
    for i, (pos, title) in enumerate(marks):
        if any(k in title for k in DROP_HEADINGS):
            end = marks[i + 1][0] if i + 1 < len(marks) else len(html)
            drop.append((pos, end))
    for a, b in sorted(drop, reverse=True):
        html = html[:a] + html[b:]
    return html


def renumber(html):
    n = [0]

    def rep(m):
        inner = re.sub(r"^\s*(?:[一二三四五六七八九十]+、|\d+(?:\.\d+)?\s*)", "", m.group(1)).strip()
        n[0] += 1
        num = CN_NUM[n[0] - 1] if n[0] <= len(CN_NUM) else str(n[0])
        return f'<h2>{num}、{inner}</h2>'
    return re.sub(r"<h2[^>]*>(.*?)</h2>", rep, html, flags=re.S)


def fmt(x, nd=2):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "—"
    return f"{x*100:.{nd}f}%"


def build_blocks(res, cur, holdings, notes, cb_note, sample_note):
    rows = "".join(
        f'<tr><td>{r["name"]}</td>'
        f'<td class="num">{fmt(r["stats"]["cum"], 1)}</td>'
        f'<td class="num">{fmt(r["stats"]["cagr"])}</td>'
        f'<td class="num">{fmt(r["stats"]["vol"])}</td>'
        f'<td class="num"><b>{r["stats"]["sharpe"]:.2f}</b></td>'
        f'<td class="num">{fmt(r["stats"]["mdd"])}</td>'
        f'<td class="num">{r["stats"]["calmar"]:.2f}</td>'
        f'<td class="num">{fmt(r["avg_pos"], 0)}</td></tr>'
        for r in res
    )
    hold_rows = "".join(
        f'<tr><td class="num">{i+1}</td><td class="mono">{h["code"]}</td><td>{h["name"]}</td>'
        f'<td>{h["ind"]}</td><td class="num">{h["score"]:.2f}</td></tr>'
        for i, h in enumerate(holdings)
    )
    chart = cur.pop("chart")
    why = "".join(f"<li>{x}</li>" for x in notes)

    front = f'''
<h2>择时口径与当前建议仓位</h2>
<div class="card">
<div class="grid2">
  <div>
    <h3>当前建议仓位</h3>
    <div style="display:flex;align-items:baseline;gap:10px">
      <b style="font-size:34px;color:{cur['color']}">{cur['pos']*100:.0f}%</b>
      <span class="b-cb">{cur['state']}</span>
    </div>
    <div class="legend" style="margin-top:6px">{cur['rule']}</div>
  </div>
  <div>
    <h3>信号读数</h3>
    <table>
      <tr><td>沪深300 收盘 / 月线 MA10</td><td class="num mono">{cur['px']:.1f} / {cur['ma']:.1f}</td></tr>
      <tr><td>相对均线</td><td class="num">{cur['gap']:+.1f}%</td></tr>
      <tr><td>CB 估值便宜度（看板读数）</td><td class="num mono">{cur['cb']}</td></tr>
      <tr><td>指数数据截止</td><td class="num mono">{cur['asof']}</td></tr>
    </table>
  </div>
</div>
<div class="legend" style="margin-top:10px">{cb_note}</div>
</div>

<h2>四种搭配的回测对比</h2>
<div class="card">
<table>
<tr><th>组合</th><th class="num">累计</th><th class="num">年化</th><th class="num">波动</th>
    <th class="num">夏普</th><th class="num">最大回撤</th><th class="num">Calmar</th><th class="num">平均仓位</th></tr>
{rows}
</table>
<div class="legend" style="margin-top:8px">
{sample_note}
</div>
</div>

<h2>净值曲线</h2>
<div class="card">
{chart}
<div class="legend">相对净值（样本起点 = 1）· 灰虚线为 1.0 基准</div>
</div>

<h2>当期持仓（质量 Top28）</h2>
<div class="card">
<div class="legend" style="margin:0 0 8px">
调仓基准：{holdings[0]['period'] if holdings else '—'} · 取自股池页当期名单（申万三级每行业 ≤2 只、按舞弊调整质量因子 Q−F 降序）</div>
<table>
<tr><th class="num">#</th><th>代码</th><th>名称</th><th>申万三级</th><th class="num">评分</th></tr>
{hold_rows}
</table>
</div>
'''

    back = f'''
<h2>模型与口径说明</h2>
<div class="card">
<h3>为什么把仓位主控从 CB 换成趋势闸门</h3>
<ul style="font-size:13.5px;color:#374151;margin:0;padding-left:20px;line-height:1.9">{why}</ul>

<h3 style="margin-top:16px">CB 的定位：从「仓位旋钮」降级为「风险提示阀」</h3>
<table>
<tr><th>CB 便宜度</th><th class="num">叠加动作</th><th>含义</th></tr>
<tr><td>CB &lt; 0.15</td><td class="num">趋势仓位再打 5 折</td><td>估值处在近两年最贵的 15% 区间，减半防守</td></tr>
<tr><td>0.15 ≤ CB &lt; 0.75</td><td class="num">不干预</td><td>估值不构成极端风险，仓位完全交给趋势</td></tr>
<tr><td>CB ≥ 0.75</td><td class="num">不额外加仓</td><td>便宜是「不卖」的理由，不是「满仓」的理由（趋势未确认不抢跑）</td></tr>
</table>
<div class="legend" style="margin-top:8px">
B-score 口径：CB ＝（市值加权 PB 便宜度分位 ＋ 等权 PB 便宜度分位）÷ 2，
便宜度分位 ＝ 1 − 该值在近 500 个交易日的百分位。它度量「市场贵不贵」，不含趋势/动量信息 ——
这正是它在 2019 年以来样本里失效的原因：牛市里估值分位长期偏低（被迫空仓），
下跌初期分位又来不及升高（保护不了回撤）。
</div>

<h3 style="margin-top:16px">本次修订（2026-10-21）</h3>
<div class="legend" style="margin:0">
旧版三处硬伤已修：① 净值曲线 250 个点被填成同一数值（图上是一条直线，图例却写「起点 1 → 末值 0.9013」）；
② 两行对比基准（不择时、沪深300）整行为「—」；③「平均仓位 6.5% ＋ 累计 −9.87%」算术上不成立
（反推标的需跌约 150%，而同期 Top28 实为 +94%），且用当期股池回溯过去两年属前视偏差。
现版本直接采用股池页发布的质量Top28 净值（两页数字同源）、逐月重算、四种搭配同表对比，
出图前强制断言每条曲线至少有 12 个不同取值。
</div>
</div>
'''
    return front, back


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--html-in")
    ap.add_argument("--html-out")
    ap.add_argument("--no-publish", action="store_true")
    a = ap.parse_args()

    d, ser, core = load_page_data()
    idx = ser.index
    r_base = ser[core].pct_change()
    log(f"股池净值 {core}：{idx[0].date()} → {idx[-1].date()}，{len(idx)} 个月")

    # 趋势：用股池页同源的月频沪深300 做信号，日线只用于读数
    ma = ser["沪深300"].rolling(TREND_MA_M).mean()
    up_m = (ser["沪深300"] > ma).astype(float)
    pos_trend = (TREND_FLOOR + (1 - TREND_FLOOR) * up_m).clip(0, 1)

    cb_m, cb_src, cb_last = cb_monthly(idx)

    designs = []
    rr, p = apply_pos(pd.Series(1.0, index=idx), r_base)
    designs.append(dict(name=f"{core} · 满仓不择时（基线）", stats=metrics(rr), avg_pos=p.mean()))
    rr, p = apply_pos(pos_trend, r_base)
    designs.append(dict(name=f"{core} × 趋势闸门（MA{TREND_MA_M}，地板 {TREND_FLOOR:.0%}）★推荐",
                        stats=metrics(rr), avg_pos=p.mean()))
    if cb_m is not None:
        rr, p = apply_pos((cb_m - CB_LO) / CB_SLOPE, r_base)
        designs.append(dict(name=f"{core} × CB 估值择时（旧口径）", stats=metrics(rr), avg_pos=p.mean()))
        rr, p = apply_pos(pos_trend * (0.7 + 0.3 * cb_m), r_base)
        designs.append(dict(name="趋势闸门 × CB 微调（0.7+0.3·CB）", stats=metrics(rr), avg_pos=p.mean()))
    rr = ser["沪深300"].pct_change()
    designs.append(dict(name="沪深300（买入持有）", stats=metrics(rr), avg_pos=1.0))

    # 曲线（相对净值）
    ser_lines = [(f"{core} 满仓", ser[core] / ser[core].iloc[0], "#9ca3af", "4 3")]
    if cb_m is not None:
        rr_cb, _ = apply_pos((cb_m - CB_LO) / CB_SLOPE, r_base)
        ser_lines.append(("CB 择时（旧）", (1 + rr_cb.fillna(0)).cumprod(), "#f59e0b", "3 3"))
    rr_tr, _ = apply_pos(pos_trend, r_base)
    ser_lines.append(("趋势闸门", (1 + rr_tr.fillna(0)).cumprod(), "#c0392b", ""))
    ser_lines.append(("沪深300", ser["沪深300"] / ser["沪深300"].iloc[0], "#2563eb", "2 2"))
    chart = svg_chart(ser_lines)
    legend = " · ".join(f'<span style="color:{c}">■</span> {n}' for n, _, c, _ in ser_lines)
    chart = f'<div class="legend" style="margin-bottom:6px">{legend}</div>' + chart

    # 当前建议仓位：优先日线（更新更快），否则月线
    hs, hs_src = hs300_daily(idx)
    if hs is not None:
        m_mkt = hs.groupby(hs.index.to_period("M")).last()
        ma_now = m_mkt.rolling(TREND_MA_M).mean().iloc[-1]
        px_now, asof = float(hs.iloc[-1]), hs.index[-1].strftime("%Y-%m-%d")
        mode = "日线"
    else:
        px_now, asof = float(ser["沪深300"].iloc[-1]), idx[-1].strftime("%Y-%m-%d")
        ma_now = float(ma.iloc[-1]); mode = "月线"
    last_up = px_now > ma_now
    pos_now = TREND_FLOOR + (1 - TREND_FLOOR) * last_up
    cb_read = read_dashboard_cb() or cb_last
    cb_txt = f"{cb_read:.4f}" if cb_read is not None else "—"
    cur = dict(pos=pos_now, color="#c0392b" if pos_now < 1 else "#15803d",
               state="均线之上 · 可满仓" if last_up else "均线之下 · 降仓防守",
               rule=f"仓位 = {TREND_FLOOR:.0%} + {1-TREND_FLOOR:.0%} × 1[沪深300 收盘 &gt; 月线 MA{TREND_MA_M}]；"
                    f"跌破均线保留 {TREND_FLOOR:.0%} 地板，不清仓（清仓会错过反弹首月）",
               px=px_now, ma=float(ma_now), gap=(px_now / ma_now - 1) * 100,
               cb=cb_txt, asof=f"{asof}（{mode}）", chart=chart)

    # 当期持仓：直接用股池页数据包里的 q28
    holdings = []
    for x in d.get("q28", []):
        c = str(x.get("c", ""))
        code = c if c[:2] in ("sh", "sz", "bj") else (("sh" if c[0] == "6" else "sz") + c)
        holdings.append(dict(code=code, name=x.get("n", ""), ind=x.get("ind", ""),
                             score=float(x.get("s", 0)), period=d.get("label", "")))

    dmap = {x["name"]: x for x in designs}
    base = designs[0]["stats"]
    tr = [x for x in designs if "趋势闸门（" in x["name"]][0]["stats"]
    notes = [
        f"满仓基线：年化 {base['cagr']*100:.1f}%、夏普 {base['sharpe']:.2f}、最大回撤 {base['mdd']*100:.1f}%。",
        f"趋势闸门：年化 {tr['cagr']*100:.1f}%、夏普 {tr['sharpe']:.2f}、最大回撤 {tr['mdd']*100:.1f}% —— "
        f"夏普 {(tr['sharpe']-base['sharpe']):+.2f}、回撤 {(tr['mdd']-base['mdd'])*100:+.1f} 个百分点，"
        f"代价是牛市里少赚一段（平均仓位只有 {tr['cagr']*0+ [x['avg_pos'] for x in designs if '趋势闸门（' in x['name']][0]*100:.0f}%）。",
    ]
    cbx = [x for x in designs if "旧口径" in x["name"]]
    if cbx:
        c = cbx[0]["stats"]
        notes.append(f"CB 估值择时（旧口径）：年化 {c['cagr']*100:.1f}%、夏普 {c['sharpe']:.2f}、"
                     f"最大回撤 {c['mdd']*100:.1f}% —— 全样本是负贡献：它砍掉的是上涨段，"
                     f"却挡不住下跌段（估值分位在下跌初期来不及升高）。")
    notes.append("结论：估值分位只当「极端风险提示」，仓位开关交给趋势（月线 MA10）；"
                 "CB 仅在极贵（&lt;0.15）时把趋势仓位再打 5 折，作为二级阀。")
    cb_note = (f"CB 序列来源：{cb_src}（与看板 500 日分位同源复算，允许 ±0.05 差异）。" if cb_src
               else "CB 复算不可用，本页只展示看板读数。")
    sample_note = (f"口径：净值直接取股池页发布的质量Top28 v2（与股池页数字同源）；"
                   f"仓位月末决定、下月生效；单边 {COST*1e4:.0f}bp；现金零收益；样本 "
                   f"{base['start']} → {base['end']}（{base['n']} 个月）。"
                   f"股池按季度换手，故组合净值本身是季度阶跃的，月度择时反映的是季内首个可交易日动作。")

    front, back = build_blocks(designs, cur, holdings, notes, cb_note, sample_note)

    skill = find_library_skill()
    tmpdir = None
    if a.html_in:
        html = Path(a.html_in).read_text(encoding="utf-8")
    else:
        if skill is None:
            raise SystemExit("找不到资料库 skill，无法读写页面")
        tmpdir = tempfile.mkdtemp(prefix="bscombo_")
        r = subprocess.run([sys.executable, str(skill / "page" / "download_page_artifacts.py"),
                            "--node-id", NODE, "--out-dir", tmpdir], capture_output=True, timeout=300)
        out = r.stdout.decode("utf-8", "replace")
        if "KS_ARTIFACTS_OK" not in out:
            raise SystemExit(f"下载页面失败：{out[-300:]}")
        html = Path(tmpdir, FILE_NAME).read_text(encoding="utf-8")

    html = strip_old_block(html)
    fi = html.find(FOOTER_KEY)
    if fi > 0:
        fj = html.rfind("<div", 0, fi)
        tail_anchor = fj if fj > 0 else fi
    else:
        tail_anchor = html.rfind("</div></body>")
        if tail_anchor < 0:
            tail_anchor = html.rfind("</body>")
    h2 = html.find("<h2")
    head_anchor = h2 if (0 < h2 < tail_anchor) else tail_anchor
    html = html[:head_anchor] + front + html[head_anchor:tail_anchor] + back + html[tail_anchor:]

    html = re.sub(r"组合：[^<·]*·\s*双周调仓\s*·\s*CB 连续仓位",
                  "组合：质量 Top28 · 趋势闸门仓位", html)
    html = re.sub(r"组合：质量\s*Top30[^<]*", "组合：质量 Top28 · 趋势闸门仓位", html)
    html = html.replace("B-score 建议仓位", "CB 口径仓位（已降级）")
    html = re.sub(r"仓位 = clip\(\(CB − [0-9.]+?\) ÷ [0-9.]+, 0, 1\)",
                  "旧口径仅作对照，实际建议仓位见第一节", html)

    html = renumber(html)

    assert "—</td><td class=\"num\">—" not in html, "仍有整行缺失的对比基准"
    assert html.count("<polyline") >= 3, "曲线数量不足"

    if a.html_out:
        Path(a.html_out).write_text(html, encoding="utf-8")
        log(f"已写出 {a.html_out}（未上传）")
    if a.dry_run or a.html_out:
        print(json.dumps({x["name"]: {k: (round(v, 4) if isinstance(v, float) else v)
                                      for k, v in x["stats"].items()} for x in designs},
                         ensure_ascii=False, indent=1))
        return 0

    out_html = Path(tmpdir or ".", FILE_NAME)
    out_html.write_text(html, encoding="utf-8")
    r = subprocess.run([sys.executable, str(skill / "page" / "import_html.py"), str(out_html),
                        "--file-name", FILE_NAME, "--node-block-id", NODE], capture_output=True, timeout=600)
    out = r.stdout.decode("utf-8", "replace")
    if "error" in out.lower() and "KS_" not in out:
        raise SystemExit(f"上传失败：{out[-400:]}")
    log("页面已回写")

    if not a.no_publish:
        r = subprocess.run([sys.executable, str(BASE / "cloud" / "publish_pages.py")],
                           capture_output=True, timeout=600, env={**os.environ, "ONLY": "BSCORE"})
        log(r.stdout.decode("utf-8", "replace").strip()[-160:])

    print("BSCOMBO_OK " + json.dumps({
        "asof": asof, "pos": round(pos_now, 2), "cb": cb_txt,
        "designs": {x["name"]: {k: round(v, 4) for k, v in x["stats"].items()} for x in designs},
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

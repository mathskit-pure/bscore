#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""search_scheme.py —— 搜索「分位算法 × 阈值」组合，找出最能复现原信号时点的方案。

背景：expanding percentile 从 2018-01 起算会有一个致命的热启动期——
前一两年的历史样本太少，分位长期贴顶（2018 年 CB≥0.75 占 76%），
导致「去重叠」后 2018 全年被合并成一次信号，把真实的 2018-08 吞掉了。

这里对比几种常见做法：
    exp_minN    expanding，但至少累积 N 个样本才开始出分位（N=250/500/750）
    rollN       滚动 N 个交易日的窗口分位（750≈3年，1250≈5年，1750≈7年）

评价标准：原看板第五章的 5 次信号（2018-08 / 2019-08 / 2022-04 / 2023-10 / 2025-01）
          能否被复现，以及总触发次数是否接近（原版 8.8 年 5 次）。

用法：python3 search_scheme.py --panel panel.csv
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
from pathlib import Path

REFERENCE = ["2018-08", "2019-08", "2022-04", "2023-10", "2025-01"]
BM_KEY, PB_KEY = "bm_median_all", "pb_cap50_all"


def load_panel(path: Path):
    with path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    rows.sort(key=lambda r: r["date"])
    return rows


def flt(x):
    try:
        v = float(x)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def rank(window, cur, higher_is_cheaper):
    hist = [x for x in window if x is not None]
    if not hist:
        return None
    if higher_is_cheaper:
        return sum(1 for x in hist if x <= cur) / len(hist)
    return sum(1 for x in hist if x >= cur) / len(hist)


def build(rows, scheme: str, min_n: int = 250):
    bm = [flt(r.get(BM_KEY)) for r in rows]
    pb = [flt(r.get(PB_KEY)) for r in rows]
    out = []
    n = len(rows)
    for i in range(n):
        if scheme.startswith("roll"):
            w = int(scheme[4:])
            lo = max(0, i - w + 1)
            win_bm, win_pb = bm[lo:i + 1], pb[lo:i + 1]
            if i - lo + 1 < max(60, w // 4):
                out.append(None)
                continue
        else:
            if i + 1 < min_n:          # 热启动期不出分位
                out.append(None)
                continue
            win_bm, win_pb = bm[:i + 1], pb[:i + 1]
        if bm[i] is None or pb[i] is None:
            out.append(None)
            continue
        p1 = rank(win_bm, bm[i], True)
        p2 = rank(win_pb, pb[i], False)
        out.append(None if p1 is None or p2 is None else {
            "date": rows[i]["date"], "cb": (p1 + p2) / 2,
            "bm_pct": p1, "pb_pct": p2})
    return out


def episodes(ser, th, gap=20):
    hits = [(r["date"], r["cb"]) for r in ser if r and r["cb"] >= th]
    if not hits:
        return []
    out, cur = [], [hits[0]]
    for d, v in hits[1:]:
        if (dt.date.fromisoformat(d) - dt.date.fromisoformat(cur[-1][0])).days > gap:
            out.append(cur)
            cur = []
        cur.append((d, v))
    out.append(cur)
    return out


def idx(ym):
    y, m = ym.split("-")
    return int(y) * 12 + int(m)


def score(months, ref):
    hit, used = 0, set()
    for r in ref:
        for i, m in enumerate(months):
            if i in used:
                continue
            if abs(idx(m) - idx(r)) <= 1:
                hit += 1
                used.add(i)
                break
    return hit, len(months) - len(used)


SCHEMES = ["exp_min250", "exp_min500", "exp_min750", "roll750", "roll1250", "roll1750"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default="panel.csv")
    a = ap.parse_args()
    rows = load_panel(Path(a.panel))
    print(f"面板 {len(rows)} 天 {rows[0]['date']} → {rows[-1]['date']}")
    print(f"参照：{', '.join(REFERENCE)}（原版 8.8 年共 5 次）\n")

    best = []
    for sc in SCHEMES:
        min_n = int(sc.split("min")[1]) if sc.startswith("exp") else 250
        ser = build(rows, sc, min_n)
        valid = sum(1 for r in ser if r)
        for i in range(50, 100):
            th = i / 100
            ms = [e[0][0][:7] for e in episodes(ser, th)]
            if not ms:
                continue
            hit, extra = score(ms, REFERENCE)
            best.append((hit, -abs(len(ms) - 5), extra, sc, th, ms, valid))

    best.sort(key=lambda r: (-r[0], -r[1], r[2]))
    print(f"{'方案':<12}{'阈值':>6}{'命中':>5}{'次数':>5}{'多发':>5}  触发月份")
    print("-" * 96)
    for hit, negdist, extra, sc, th, ms, valid in best[:12]:
        print(f"{sc:<12}{th:>6.2f}{hit:>5}{len(ms):>5}{extra:>5}  {', '.join(ms)}")

    h, nd, ex, sc, th, ms, valid = best[0]
    print("-" * 96)
    print(f"\n最佳：{sc} 阈值 {th:.2f} —— 命中 {h}/5，共 {len(ms)} 次触发（有效样本 {valid} 天）")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""compute_cb.py —— 从日频聚合面板算分位与 CB，并反推原实现用的是哪一套口径。

已知 2026-10-16 页面读数：
    BM 便宜度分位 = 0.637
    PB 便宜分位   = 0.481
    CB            = 0.559   （正好是两者平均）

做法：把候选的「BM 序列 × PB 序列 × 分位定义」组合全部算一遍，
看哪一组能在 10-16 同时逼近这三个数。找到的就是原口径。

用法：
    python3 compute_cb.py --panel panel.csv --date 2026-10-16
    python3 compute_cb.py --panel panel.csv --date 2026-10-16 --export cb_series.csv
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

BM_SERIES = ["bm_median_all", "bm_median_flt"]
PB_SERIES = ["pb_cap50_all", "pb_cap50_flt", "pb_median_all", "pb_median_flt",
             "pb_weighted_all", "pb_weighted_flt"]

TARGET = {"bm_pct": 0.637, "pb_pct": 0.481, "cb": 0.559}


def load(panel: Path):
    with panel.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    rows.sort(key=lambda r: r["date"])
    return rows


def as_float(v):
    try:
        x = float(v)
        return x if x > 0 else None
    except (TypeError, ValueError):
        return None


def pct_rank_history(series, i, higher_is_cheaper: bool) -> float:
    """第 i 个值在 [0..i] 历史里的分位（0~1）。

    higher_is_cheaper=True：值越大越便宜，分位 = 历史中 <= 当前 的占比。
    higher_is_cheaper=False：值越小越便宜（如 PB），分位 = 历史中 >= 当前 的占比。
    """
    cur = series[i]
    hist = [x for x in series[: i + 1] if x is not None]
    if not hist:
        return None
    if higher_is_cheaper:
        return sum(1 for x in hist if x <= cur) / len(hist)
    return sum(1 for x in hist if x >= cur) / len(hist)


def build(rows, bm_key, pb_key):
    bm = [as_float(r.get(bm_key)) for r in rows]
    pb = [as_float(r.get(pb_key)) for r in rows]
    out = []
    for i in range(len(rows)):
        if bm[i] is None or pb[i] is None:
            out.append(None)
            continue
        p_bm = pct_rank_history(bm, i, higher_is_cheaper=True)
        p_pb = pct_rank_history(pb, i, higher_is_cheaper=False)
        if p_bm is None or p_pb is None:
            out.append(None)
            continue
        out.append({"date": rows[i]["date"], "bm": bm[i], "pb": pb[i],
                    "bm_pct": p_bm, "pb_pct": p_pb, "cb": (p_bm + p_pb) / 2})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default="panel.csv")
    ap.add_argument("--date", default="2026-10-16")
    ap.add_argument("--export", default="")
    a = ap.parse_args()

    rows = load(Path(a.panel))
    print(f"面板：{len(rows)} 天，{rows[0]['date']} → {rows[-1]['date']}\n")

    target_idx = next((i for i, r in enumerate(rows) if r["date"] == a.date), None)
    if target_idx is None:
        raise SystemExit(f"面板里没有 {a.date}")

    print(f"{'BM 序列':<18}{'PB 序列':<18}{'BM分位':>8}{'PB分位':>8}{'CB':>8}{'误差':>8}")
    print(f"{'【目标】':<18}{'':<18}{TARGET['bm_pct']:>8.3f}{TARGET['pb_pct']:>8.3f}{TARGET['cb']:>8.3f}")
    print("-" * 68)

    results = []
    for bm_key in BM_SERIES:
        for pb_key in PB_SERIES:
            ser = build(rows, bm_key, pb_key)
            cur = ser[target_idx]
            if not cur:
                continue
            err = abs(cur["bm_pct"] - TARGET["bm_pct"]) + abs(cur["pb_pct"] - TARGET["pb_pct"])
            results.append((err, bm_key, pb_key, cur, ser))
            print(f"{bm_key:<18}{pb_key:<18}{cur['bm_pct']:>8.3f}{cur['pb_pct']:>8.3f}"
                  f"{cur['cb']:>8.3f}{err:>8.3f}")

    results.sort(key=lambda x: x[0])
    print("-" * 68)
    best = results[0]
    print(f"\n最接近的一组：BM={best[1]}  PB={best[2]}  合并误差 {best[0]:.3f}")
    print(f"  {a.date}：BM分位 {best[3]['bm_pct']:.3f}（目标 {TARGET['bm_pct']}）"
          f"  PB分位 {best[3]['pb_pct']:.3f}（目标 {TARGET['pb_pct']}）"
          f"  CB {best[3]['cb']:.3f}（目标 {TARGET['cb']}）")

    if a.export:
        ser = best[4]
        out = Path(a.export)
        with out.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["date", "bm", "pb", "bm_pct", "pb_pct", "cb"])
            w.writeheader()
            for r in ser:
                if r:
                    w.writerow({k: (round(v, 6) if isinstance(v, float) else v) for k, v in r.items()})
        print(f"\nCB 序列已导出：{out}（{sum(1 for r in ser if r)} 行）")


if __name__ == "__main__":
    main()

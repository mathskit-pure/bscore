#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""daily_update_v2.py —— B-score 重建版每日计算（口径 v2）。

与原实现的差别（必须有意识）：
    原：expanding percentile since 2005      新：滚动 5 年（1250 交易日）分位
    原阈值：建仓 0.75 / 满仓 0.80            新阈值：建仓 0.85 / 满仓 0.92
    （新阈值按「触发频次与原版相当」标定：旧阈值在新分布下会 27% 的交易日触发）

口径（已用 2026-10-16 看板读数验证）：
    · 标的池：全 A 剔除金融/ST，PB>0 且总市值>0
    · BM 中位数 = 1 / median(全市场 PB)            → 0.397908 vs 原 0.397
    · 全市场 PB = 去极值均值（PB≤50 截断）           → 3.8115 vs 原 3.82
    · 加权 PB = Σ总市值 / Σ净资产                   → 2.351 vs 原 2.25
    · CB = (BM 便宜度分位 + PB 便宜分位) / 2

用法：
    python3 daily_update_v2.py                  # 计算最新一个交易日，写 latest_v2.json
    python3 daily_update_v2.py --date 2026-10-16   # 指定日期（用于校验）
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import urllib.request
from pathlib import Path

import build_panel as bp
from search_scheme import load_panel

UA = {"User-Agent": "Mozilla/5.0"}
WINDOW = 1250          # 滚动窗口（交易日）
MIN_PTS = 312          # 出分位所需最少样本
T_BUILD, T_FULL = 0.85, 0.92

INDEXES = {"hs300": ("sh000300", "沪深300"), "zz500": ("sh000905", "中证500"),
           "zz1000": ("sh000852", "中证1000")}


def fetch_index(symbol: str, n: int = 300):
    url = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           f"CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=no&datalen={n}")
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        data = json.loads(r.read().decode("utf-8"))
    return [(d["day"], float(d["close"])) for d in data]


def index_stats(symbol: str):
    ser = fetch_index(symbol)
    if len(ser) < 2:
        return None
    day, close = ser[-1]
    prev = ser[-2][1]
    highs = [c for _, c in ser[-250:]]
    return {"date": day, "close": close, "chg": (close / prev - 1) * 100,
            "dd": (close / max(highs) - 1) * 100}


def pct_from_window(window, cur, higher_is_cheaper):
    hist = [x for x in window if x is not None]
    if not hist or cur is None:
        return None
    if higher_is_cheaper:
        return sum(1 for x in hist if x <= cur) / len(hist)
    return sum(1 for x in hist if x >= cur) / len(hist)


def quantile(window, q):
    s = sorted(x for x in window if x is not None)
    if not s:
        return None
    return s[min(len(s) - 1, max(0, int(q * (len(s) - 1))))]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default="panel.csv")
    ap.add_argument("--date", default="")
    ap.add_argument("--out", default="latest_v2.json")
    ap.add_argument("--refresh", action="store_true", help="若面板缺目标日期则联网补采")
    a = ap.parse_args()

    panel = Path(a.panel)
    rows = load_panel(panel)
    target = a.date or rows[-1]["date"]

    if a.date and a.date > rows[-1]["date"]:
        if not a.refresh:
            raise SystemExit(f"面板最新为 {rows[-1]['date']}，缺少 {a.date}；加 --refresh 联网补采")
        agg = bp.aggregate(a.date, bp.fetch_day(a.date))
        if not agg:
            raise SystemExit(f"{a.date} 不是交易日或取数失败")
        with panel.open("a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=bp.OUT_FIELDS).writerow(agg)
        rows = load_panel(panel)
        print(f"已补采 {a.date}")

    idx = next((i for i, r in enumerate(rows) if r["date"] == target), None)
    if idx is None:
        raise SystemExit(f"面板里没有 {target}")

    bm = [bp_flt(r.get("bm_median_all")) for r in rows]
    pb = [bp_flt(r.get("pb_cap50_all")) for r in rows]
    lo = max(0, idx - WINDOW + 1)
    win_bm, win_pb = bm[lo:idx + 1], pb[lo:idx + 1]
    if len([x for x in win_bm if x]) < MIN_PTS:
        raise SystemExit(f"窗口内样本不足（{len(win_bm)} < {MIN_PTS}），无法出分位")

    bm_pct = pct_from_window(win_bm, bm[idx], True)
    pb_pct = pct_from_window(win_pb, pb[idx], False)
    cb = (bm_pct + pb_pct) / 2

    row = rows[idx]
    n_flt = int(row["n_flt"])
    broken = float(row["broken_flt"])
    weighted = float(row["pb_weighted_flt"])

    # 触发测算：CB 是两维分位的平均，所以「建仓」需要两维合计达到 2*T。
    # 单个维度不可能时，给出「PB 已到窗口最低（分位=1）」这条最省力的路径。
    hs0 = index_stats(INDEXES["hs300"][0])
    pb_now = float(row["pb_cap50_all"])
    min_pb = quantile(win_pb, 0.0)
    need_bm_when_pb_max = 2 * T_BUILD - 1.0          # PB 分位取 1.0 时，BM 分位至少要多少
    pb_val = min_pb
    bm_val = quantile(win_bm, need_bm_when_pb_max) if need_bm_when_pb_max <= 1 else None
    trigger = {}
    if bm_val is not None and hs0:
        trigger = {
            "pb": round(pb_val, 3),
            "bm": round(bm_val, 4),
            "hs300": round(hs0["close"] * pb_val / pb_now),
            "note": f"PB 分位到 1.0（窗口最低）时，BM 分位需 {need_bm_when_pb_max:.2f}",
        }
    cap_pb_only = (bm_pct + 1.0) / 2      # 只靠 PB：CB 上限
    cap_bm_only = (1.0 + pb_pct) / 2      # 只靠 BM：CB 上限

    # 仓位建议
    if cb >= T_FULL:
        sig, pos = "满仓", 100
    elif cb >= T_BUILD:
        sig, pos = "建仓", round(min(100, 40 + (cb - T_BUILD) / (T_FULL - T_BUILD) * 60))
    elif pb_pct <= 0.15:
        sig, pos = "清仓", 0
    elif pb_pct <= 0.25:
        sig, pos = "减仓", 25
    else:
        sig, pos = "观望 / 维持常规仓位", 50

    readings = {
        "trade_date": target,
        "cb": round(cb, 3),
        "bm_median": round(bm[idx], 4),
        "bm_percentile": round(bm_pct, 3),
        "pb": round(float(row["pb_cap50_all"]), 2),
        "pb_percentile": round(pb_pct, 3),
        "broken_net": round(broken, 1),
        "weighted_pb": round(weighted, 2),
        "sample_n": n_flt,
        "index": {k: index_stats(v[0]) for k, v in INDEXES.items()},
        "trigger": trigger,
        "_meta": {"scheme": f"rolling {WINDOW}d percentile", "window_pts": len([x for x in win_bm if x]),
                  "t_build": T_BUILD, "t_full": T_FULL, "signal": sig, "position": pos,
                  "cb_cap_pb_only": round(cap_pb_only, 3),
                  "cb_cap_bm_only": round(cap_bm_only, 3)},
    }
    Path(a.out).write_bytes(json.dumps(readings, ensure_ascii=False, indent=1).encode("utf-8"))

    print(f"交易日 {target}")
    print(f"  CB {cb:.3f}　BM 中位 {bm[idx]:.4f}（便宜度分位 {bm_pct:.3f}）"
          f"　全市场 PB {row['pb_cap50_all']}（便宜分位 {pb_pct:.3f}）")
    print(f"  破净 {broken:.1f}%　加权 PB {weighted:.2f}　样本 {n_flt}")
    print(f"  信号：{sig}　建议仓位 {pos}%")
    print(f"  触发测算：若 PB 到窗口最低 {trigger.get('pb')}（分位 1.0），BM 中位需到 {trigger.get('bm')}，"
          f"对应沪深300 约 {trigger.get('hs300')}")
    print(f"        单靠 PB 最多只能把 CB 推到 {cap_pb_only:.3f}，单靠 BM 到 {cap_bm_only:.3f}")
    print(f"  已写 {a.out}")


def bp_flt(x):
    try:
        v = float(x)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    main()

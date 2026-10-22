#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""calibrate.py —— 用原看板的真实信号历史来标定新口径的阈值。

依据：原看板第五章「历史买入信号（CB ≥ 0.75，去重叠 7 次）」给出了 7 次触发的月份：
    2008-10, 2012-11, 2018-08, 2019-08, 2022-04, 2023-10, 2025-01
其中 5 次落在可用数据窗口（2018-01 起）内：2018-08 / 2019-08 / 2022-04 / 2023-10 / 2025-01。

于是标定问题变成：在新的 CB 序列上，选一个阈值 T，使「去重叠后的触发月份」
与这 5 个月尽可能重合。这样即使 CB 的绝对水平变了，信号的行为（何时喊买）仍与原版一致。

用法：python3 calibrate.py --series cb_series_v2.csv
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

REFERENCE = ["2018-08", "2019-08", "2022-04", "2023-10", "2025-01"]


def load(path: Path):
    with path.open(encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["cb"]]
    return [(r["date"], float(r["cb"])) for r in rows]


def episodes(series, th: float, gap_days: int = 20):
    """CB ≥ th 的连续段。两段间隔超过 gap_days 天算两次独立信号。"""
    import datetime as dt

    hits = [(d, v) for d, v in series if v >= th]
    if not hits:
        return []
    out, cur = [], [hits[0]]
    for d, v in hits[1:]:
        prev = dt.date.fromisoformat(cur[-1][0])
        if (dt.date.fromisoformat(d) - prev).days > gap_days:
            out.append(cur)
            cur = []
        cur.append((d, v))
    out.append(cur)
    return out


def months_of(eps):
    return [e[0][0][:7] for e in eps]


def score(months, ref):
    """命中数（同一月或相差 1 个月算命中），再减去多发信号的惩罚。"""
    import datetime as dt

    def idx(ym):
        y, m = ym.split("-")
        return int(y) * 12 + int(m)

    hit = 0
    used = set()
    for r in ref:
        for i, m in enumerate(months):
            if i in used:
                continue
            if abs(idx(m) - idx(r)) <= 1:
                hit += 1
                used.add(i)
                break
    extra = len(months) - len(used)
    return hit, extra


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--series", default="cb_series_v2.csv")
    ap.add_argument("--gap-days", type=int, default=20)
    a = ap.parse_args()

    series = load(Path(a.series))
    print(f"序列 {len(series)} 天：{series[0][0]} → {series[-1][0]}")
    print(f"参照信号（窗口内 5 次）：{', '.join(REFERENCE)}\n")
    print(f"{'阈值':>6}{'触发次数':>8}{'命中':>6}{'多发':>6}{'触发月份'}")
    print("-" * 78)

    best = None
    results = []
    for i in range(50, 100):
        th = i / 100
        eps = episodes(series, th, a.gap_days)
        ms = months_of(eps)
        hit, extra = score(ms, REFERENCE)
        results.append((hit, -extra, th, ms))
        if len(ms) <= 12:
            print(f"{th:>6.2f}{len(ms):>8}{hit:>6}{extra:>6}   {', '.join(ms) if ms else '—'}")

    # 排序：命中多优先，其次多发少
    results.sort(key=lambda r: (-r[0], -r[1]))
    best = results[0]
    print("-" * 78)
    print(f"\n最佳阈值 {best[2]:.2f}：命中 {best[0]}/5，触发 {len(best[3])} 次")
    print(f"  触发月份：{', '.join(best[3])}")

    # 给出同命中数下的阈值区间
    same = [r for r in results if r[0] == best[0]]
    print(f"  同命中数的阈值区间：{min(r[2] for r in same):.2f} ~ {max(r[2] for r in same):.2f}")


if __name__ == "__main__":
    main()

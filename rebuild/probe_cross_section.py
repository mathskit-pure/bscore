#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""probe_cross_section.py —— 拉某一天的全市场估值横截面，试算各种口径。

目的：看板 v23 在 2026-10-16 这一天记录了这些读数——
    全市场 PB = 3.82，加权 PB = 2.25，破净率 = 7.2%，有效样本 = 5269，
    BM 中位数当前值 = 0.397
本脚本用东财的逐股 PB_MRQ 与总市值复算，比较哪种口径能对上，
从而反推原实现的定义。

用法：python3 probe_cross_section.py --date 2026-10-16
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.parse
import urllib.request

UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"}
API = "https://datacenter-web.eastmoney.com/api/data/v1/get"
COLS = "SECURITY_CODE,TRADE_DATE,PB_MRQ,TOTAL_MARKET_CAP,CLOSE_PRICE,TOTAL_SHARES"


def fetch_page(date: str, page: int, size: int = 1000):
    q = {
        "reportName": "RPT_VALUEANALYSIS_DET",
        "columns": COLS,
        "filter": f"(TRADE_DATE='{date}')",
        "pageNumber": page,
        "pageSize": size,
        "sortColumns": "SECURITY_CODE",
        "sortTypes": 1,
    }
    url = API + "?" + urllib.parse.urlencode(q, safe="(),='$")
    for i in range(3):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=40) as r:
                j = json.loads(r.read().decode("utf-8"))
            res = j.get("result")
            if not res:
                return [], 0
            return res["data"], res.get("pages", 0)
        except Exception as e:
            if i == 2:
                print(f"  第{page}页失败：{e}")
                return [], 0
            time.sleep(1.5)
    return [], 0


def fetch_day(date: str):
    rows, pages = fetch_page(date, 1)
    if not rows:
        return []
    allrows = list(rows)
    for p in range(2, pages + 1):
        more, _ = fetch_page(date, p)
        allrows.extend(more)
        time.sleep(0.15)
    return allrows


def summarize(rows):
    """按几种候选口径分别算，便于对照。"""
    def ok(r):
        pb = r.get("PB_MRQ")
        mv = r.get("TOTAL_MARKET_CAP")
        return pb is not None and mv is not None and pb > 0 and mv > 0

    good = [r for r in rows if ok(r)]
    pbs = [r["PB_MRQ"] for r in good]
    mvs = [r["TOTAL_MARKET_CAP"] for r in good]
    # 净资产 = 总市值 / PB
    nas = [r["TOTAL_MARKET_CAP"] / r["PB_MRQ"] for r in good]

    out = {
        "总条数": len(rows),
        "PB 有效条数": len(good),
        "PB 中位数": round(statistics.median(pbs), 4),
        "PB 均值": round(statistics.fmean(pbs), 4),
        "PB 中位数(仅 PB<20)": round(statistics.median([x for x in pbs if x < 20]), 4),
        "1/PB 中位数": round(1 / statistics.median(pbs), 4),
        "BM 中位数(逐股取倒数的中位)": round(statistics.median([1 / x for x in pbs]), 4),
        "加权 PB (ΣMV/ΣNA)": round(sum(mvs) / sum(nas), 4),
        "破净率(PB<1, %)": round(100 * sum(1 for x in pbs if x < 1) / len(pbs), 2),
        "PB<0 或缺失的条数": len(rows) - len(good),
    }
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="2026-10-16")
    a = ap.parse_args()

    t0 = time.time()
    rows = fetch_day(a.date)
    if not rows:
        raise SystemExit("没取到数据")
    print(f"{a.date}：取到 {len(rows)} 条，耗时 {time.time()-t0:.1f}s\n")
    res = summarize(rows)
    target = {
        "全市场 PB": 3.82,
        "加权 PB": 2.25,
        "破净率": 7.2,
        "有效样本": 5269,
        "BM 中位数当前值": 0.397,
    }
    print("【复算结果】")
    for k, v in res.items():
        print(f"  {k:<26} {v}")
    print("\n【页面记录（2026-10-16）】")
    for k, v in target.items():
        print(f"  {k:<26} {v}")


if __name__ == "__main__":
    main()

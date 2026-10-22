#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_panel.py —— 逐日采集全市场估值横截面，落成日频聚合表。

数据源：东方财富 datacenter `RPT_VALUEANALYSIS_DET`
        每日约 5500~5600 条，单页 1000 条，一天 6 页。
标的池：剔除金融（银行/保险/证券/多元金融/信托）、剔除 ST、剔除 PB<=0 或市值<=0。
（依据：看板「有效样本」说明列原文就是「剔除金融/ST/停牌」，且 2026-10-16 复算 N=5244
 与页面 5269 相差 0.5%，破净率 7.15% 对 7.2%。）

每天的产物（写入 panel.csv，可断点续跑）：
    date, n, pb_median, pb_mean, pb_mean_cap50, pb_weighted, broken_net_pct,
    bm_median, sum_mv, sum_na, pb_p25, pb_p75

用法：
    python3 build_panel.py --start 2026-10-01 --end 2026-10-22          # 试跑
    python3 build_panel.py --start 2018-01-01 --end 2026-10-21 --workers 6
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"}
API = "https://datacenter-web.eastmoney.com/api/data/v1/get"
COLS = "SECURITY_CODE,SECURITY_NAME_ABBR,BOARD_NAME,PB_MRQ,TOTAL_MARKET_CAP"
FIN_KW = ("银行", "保险", "证券", "多元金融", "信托")
PAGE_SIZE = 1000

OUT_FIELDS = ["date",
              "n_all", "n_flt",
              "pb_median_all", "pb_median_flt",
              "pb_cap50_all", "pb_cap50_flt",
              "pb_weighted_all", "pb_weighted_flt",
              "broken_all", "broken_flt",
              "bm_median_all", "bm_median_flt",
              "p25_flt", "p75_flt"]

_lock = threading.Lock()


def http_json(url: str, retries: int = 3):
    for i in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            if i == retries - 1:
                raise
            time.sleep(1.2 * (i + 1))
    return None


def listing_dates(start: str, end: str):
    """生成候选日期：只跳过周末。

    东财的指数日线接口时好时坏，不再依赖它。非交易日查询会返回空结果，
    由 aggregate() 返回 None 自动跳过，代价只是每个节假日多一次请求。
    """
    import datetime as dt
    d0 = dt.date.fromisoformat(start)
    d1 = dt.date.fromisoformat(end)
    out, d = [], d0
    while d <= d1:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def fetch_day(date: str):
    probe = ("https://datacenter-web.eastmoney.com/api/data/v1/get?" + urllib.parse.urlencode(
        {"reportName": "RPT_VALUEANALYSIS_DET", "columns": COLS,
         "filter": f"(TRADE_DATE='{date}')", "pageNumber": 1, "pageSize": PAGE_SIZE,
         "sortColumns": "SECURITY_CODE", "sortTypes": 1}, safe="(),='$"))
    j = http_json(probe)
    res = (j or {}).get("result")
    if not res:
        return []
    rows = list(res["data"])
    pages = res.get("pages", 1)
    for p in range(2, pages + 1):
        url = ("https://datacenter-web.eastmoney.com/api/data/v1/get?" + urllib.parse.urlencode(
            {"reportName": "RPT_VALUEANALYSIS_DET", "columns": COLS,
             "filter": f"(TRADE_DATE='{date}')", "pageNumber": p, "pageSize": PAGE_SIZE,
             "sortColumns": "SECURITY_CODE", "sortTypes": 1}, safe="(),='$"))
        jj = http_json(url)
        rows.extend(((jj or {}).get("result") or {}).get("data") or [])
        time.sleep(0.08)
    return rows


def _stats(pairs):
    """pairs: [(pb, mv)] → 一组统计量。"""
    if not pairs:
        return None
    pbs = sorted(p for p, _ in pairs)
    mvs = [m for _, m in pairs]
    nas = [m / p for p, m in pairs]
    n = len(pbs)
    med = statistics.median(pbs)
    cap50 = [x for x in pbs if x <= 50] or pbs
    return {
        "n": n,
        "median": round(med, 4),
        "cap50": round(statistics.fmean(cap50), 4),
        "weighted": round(sum(mvs) / sum(nas), 4),
        "broken": round(100 * sum(1 for x in pbs if x < 1) / n, 4),
        "bm": round(1 / med, 6),
        "p25": round(pbs[min(n - 1, int(0.25 * (n - 1)))], 4),
        "p75": round(pbs[min(n - 1, int(0.75 * (n - 1)))], 4),
    }


def aggregate(date: str, rows):
    """同时算「全市场」与「剔除金融/ST」两套口径——哪套更接近原实现，后面用分位去判。"""
    allp, flt = [], []
    for r in rows:
        pb = r.get("PB_MRQ") or 0
        mv = r.get("TOTAL_MARKET_CAP") or 0
        if pb <= 0 or mv <= 0:
            continue
        allp.append((pb, mv))
        name = (r.get("SECURITY_NAME_ABBR") or "").upper()
        board = r.get("BOARD_NAME") or ""
        if "ST" in name or any(k in board for k in FIN_KW):
            continue
        flt.append((pb, mv))
    a = _stats(allp)
    f = _stats(flt)
    if not a or not f:
        return None
    return {
        "date": date,
        "n_all": a["n"], "n_flt": f["n"],
        "pb_median_all": a["median"], "pb_median_flt": f["median"],
        "pb_cap50_all": a["cap50"], "pb_cap50_flt": f["cap50"],
        "pb_weighted_all": a["weighted"], "pb_weighted_flt": f["weighted"],
        "broken_all": a["broken"], "broken_flt": f["broken"],
        "bm_median_all": a["bm"], "bm_median_flt": f["bm"],
        "p25_flt": f["p25"], "p75_flt": f["p75"],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2018-01-01")
    ap.add_argument("--end", default="2026-10-21")
    ap.add_argument("--out", default="panel.csv")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()

    out = Path(a.out)
    done = set()
    if out.exists():
        with out.open(encoding="utf-8") as f:
            done = {r["date"] for r in csv.DictReader(f)}
        print(f"已有 {len(done)} 天，续跑")

    dates = listing_dates(a.start, a.end)
    todo = [d for d in dates if d not in done]
    print(f"交易日 {len(dates)} 天，待采 {len(todo)} 天，{a.workers} 线程")

    new_file = not out.exists()
    fh = out.open("a", newline="", encoding="utf-8")
    w = csv.DictWriter(fh, fieldnames=OUT_FIELDS)
    if new_file:
        w.writeheader()

    t0 = time.time()
    ok = fail = 0

    def work(d):
        try:
            rows = fetch_day(d)
            agg = aggregate(d, rows)
            return d, agg, None
        except Exception as e:
            return d, None, str(e)

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for i, (d, agg, err) in enumerate(ex.map(work, todo), 1):
            if agg:
                with _lock:
                    w.writerow(agg)
                    ok += 1
            else:
                fail += 1
                print(f"  {d} 失败：{err}")
            if i % 25 == 0 or i == len(todo):
                el = time.time() - t0
                print(f"  {i}/{len(todo)}  成功{ok} 失败{fail}  用时{el:.0f}s  "
                      f"预计剩余{el/max(i,1)*(len(todo)-i):.0f}s", flush=True)
                fh.flush()
    fh.close()
    print(f"完成：成功 {ok}，失败 {fail}，写入 {out}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""probe_universe.py —— 反推「标的池」筛选规则。

已知 2026-10-16 页面读数：全市场 PB 3.82、加权 PB 2.25、破净率 7.2%、有效样本 5269、
BM 中位数 0.397（已确认 = 1/median(PB)）。

直接算全市场的结果全都偏低（PB 2.51 / 加权 1.71 / 破净 8.53% / 样本 5523），
方向一致指向「剔除了低 PB 的大权重行业与高风险股」，最可能是**剔除金融 + 剔除 ST**。
本脚本枚举几种标的池定义，看哪一种能同时对上四个数。
"""

from __future__ import annotations

import json
import statistics
import time
import urllib.parse
import urllib.request

UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"}
API = "https://datacenter-web.eastmoney.com/api/data/v1/get"
COLS = "SECURITY_CODE,SECURITY_NAME_ABBR,BOARD_NAME,PB_MRQ,TOTAL_MARKET_CAP"

FIN_KW = ("银行", "保险", "证券", "多元金融", "信托")


def fetch_page(date, page, size=1000):
    q = {"reportName": "RPT_VALUEANALYSIS_DET", "columns": COLS,
         "filter": f"(TRADE_DATE='{date}')", "pageNumber": page, "pageSize": size,
         "sortColumns": "SECURITY_CODE", "sortTypes": 1}
    url = API + "?" + urllib.parse.urlencode(q, safe="(),='$")
    for i in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40) as r:
                j = json.loads(r.read().decode("utf-8"))
            res = j.get("result")
            if not res:
                return [], 0
            return res["data"], res.get("pages", 0)
        except Exception:
            time.sleep(1.5)
    return [], 0


def fetch_day(date):
    rows, pages = fetch_page(date, 1)
    out = list(rows)
    for p in range(2, pages + 1):
        more, _ = fetch_page(date, p)
        out.extend(more)
        time.sleep(0.12)
    return out


def metrics(rows):
    good = [r for r in rows if (r.get("PB_MRQ") or 0) > 0 and (r.get("TOTAL_MARKET_CAP") or 0) > 0]
    if not good:
        return None
    pbs = [r["PB_MRQ"] for r in good]
    mvs = [r["TOTAL_MARKET_CAP"] for r in good]
    nas = [r["TOTAL_MARKET_CAP"] / r["PB_MRQ"] for r in good]
    return {
        "N": len(good),
        "PB中位": round(statistics.median(pbs), 3),
        "加权PB": round(sum(mvs) / sum(nas), 3),
        "破净%": round(100 * sum(1 for x in pbs if x < 1) / len(pbs), 2),
        "BM中位": round(1 / statistics.median(pbs), 4),
    }


def is_st(r):
    return "ST" in (r.get("SECURITY_NAME_ABBR") or "").upper()


def is_fin(r):
    b = r.get("BOARD_NAME") or ""
    return any(k in b for k in FIN_KW)


def main() -> None:
    date = "2026-10-16"
    rows = fetch_day(date)
    print(f"{date} 取到 {len(rows)} 条\n")

    cases = {
        "① 全部": lambda r: True,
        "② 剔 ST": lambda r: not is_st(r),
        "③ 剔金融": lambda r: not is_fin(r),
        "④ 剔金融+剔ST": lambda r: not (is_fin(r) or is_st(r)),
        "⑤ 剔金融+剔ST+剔北交所": lambda r: not (is_fin(r) or is_st(r) or r["SECURITY_CODE"].startswith(("8", "4", "9"))),
    }
    target = {"N": 5269, "PB中位": 3.82, "加权PB": 2.25, "破净%": 7.2, "BM中位": 0.397}

    print(f"{'标的池':<24}{'N':>7}{'PB中位':>9}{'加权PB':>9}{'破净%':>8}{'BM中位':>9}")
    for name, keep in cases.items():
        m = metrics([r for r in rows if keep(r)])
        if m:
            print(f"{name:<24}{m['N']:>7}{m['PB中位']:>9}{m['加权PB']:>9}{m['破净%']:>8}{m['BM中位']:>9}")
    print(f"{'【页面记录】':<24}{target['N']:>7}{target['PB中位']:>9}{target['加权PB']:>9}{target['破净%']:>8}{target['BM中位']:>9}")


if __name__ == "__main__":
    main()

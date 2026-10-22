#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""stale_banner.py —— 给看板加/换「数据已过期」横幅。

为什么需要：看板的每日刷新依赖云端任务，一旦任务停摆，页面会「安静地过期」——
页头只有一行小字「数据更新：2026-10-17」，很容易被当成最新读数用。
本脚本在页头下方插一条醒目的过期提示，并把数据日期与停摆原因写清楚。

横幅带 <!--STALE_BANNER:start--> / <!--STALE_BANNER:end--> 标记，
patch_v23.py 在成功写入新读数时会把它整块删掉（数据新鲜了就不该有横幅）。

用法：
    python3 stale_banner.py --html 当前页.html --out 带横幅.html \
        --data-date 2026-10-16 --since 2026-10-21 \
        --reason "云端每日更新任务自 2026-10-21 起未再执行，页面不会自动刷新"
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
from pathlib import Path

START = "<!--STALE_BANNER:start-->"
END = "<!--STALE_BANNER:end-->"
BLOCK_RE = re.compile(re.escape(START) + r".*?" + re.escape(END) + r"\s*", re.S)


def trading_days_between(d0: dt.date, d1: dt.date) -> int:
    """d0 之后到 d1（含）之间落在工作日里的天数。不含法定节假日库，只按周末扣除。"""
    n, d = 0, d0 + dt.timedelta(days=1)
    while d <= d1:
        if d.weekday() < 5:
            n += 1
        d += dt.timedelta(days=1)
    return n


def strip_banner(html: str) -> str:
    return BLOCK_RE.sub("", html)


def build_banner(data_date: str, since: str, reason: str) -> str:
    today = dt.date.today()
    try:
        d0 = dt.date.fromisoformat(data_date)
        # 只数「已经收盘」的交易日：今天还没收盘，不算在内
        miss = trading_days_between(d0, today - dt.timedelta(days=1))
        span = f"已连续 {miss} 个交易日未更新" if miss > 0 else "当日"
    except ValueError:
        span = ""
    since_txt = f"自 {since} 起" if since else ""
    return (
        START
        + '<div style="background:#fdecea;border:1px solid #f5c6c0;border-left:5px solid #c0392b;'
          'color:#a12c1f;padding:11px 15px;border-radius:8px;font-size:14px;line-height:1.6;'
          'margin:0 0 18px">'
          f'<b>⚠ 本页数据已过期</b>　数据停留在 <b>{data_date}</b> 收盘（{span}）。'
          f'{since_txt}云端更新任务未再执行，页面<b>不会</b>自动刷新，下方读数请勿当作最新信号使用。'
          f'<span style="color:#8a5a52">{("　原因：" + reason) if reason else ""}</span>'
        "</div>"
        + END
        + "\n"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--data-date", required=True, help="页面数据实际截至的交易日 YYYY-MM-DD")
    ap.add_argument("--since", default="", help="任务开始停摆的日期 YYYY-MM-DD")
    ap.add_argument("--reason", default="")
    a = ap.parse_args()

    html = Path(a.html).read_bytes().decode("utf-8")
    html = strip_banner(html)  # 幂等：重复跑不会叠出两条

    # 插在页头「数据更新：…」那一行（.sub）之后，视觉上紧贴标题
    anchor = re.search(r'<div[^>]*class="sub"[^>]*>(?:(?!</div>).)*?</div>\s*', html, re.S)
    if not anchor:
        raise SystemExit("找不到页头 .sub 锚点，未改动")
    pos = anchor.end()
    html = html[:pos] + build_banner(a.data_date, a.since, a.reason) + html[pos:]

    Path(a.out).write_bytes(html.encode("utf-8"))
    print(f"已插入过期横幅：数据 {a.data_date}，输出 {a.out}（{len(html)} 字符）")


if __name__ == "__main__":
    main()

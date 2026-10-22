#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""daily_run.py —— 口径 v2 的每日一条命令。

流程：
    1. 取当日全市场横截面（东财），追加到 panel.csv
    2. 重算滚动 5 年分位序列 → cb_series_v2.csv
    3. 算当日读数 → latest_v2.json（含触发测算、仓位建议）
    4. 以「线上当前页」为基底生成新版页面（锚点失效时回退模板）
    5. 走资料库编辑事务：upload → commit → publish
    6. 把 panel.csv / cb_series_v2.csv / latest_v2.json 提交并推送到 GitHub

用法：
    python3 daily_run.py --dry-run     # 只算+出页，不写线上、不推仓库
    python3 daily_run.py               # 完整跑一遍
    python3 daily_run.py --date 2026-10-21

注意：本脚本需要带 matplotlib 的解释器；写页面依赖资料库 skill 脚本的本地路径。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
NODE_ID = "VLeP5CSpUchSY3qawGq88W"
FILE_NAME = "A股B-score择时信号看板.html"
REPO = HERE.parent / "bscore"
SKILL_CANDIDATES = [
    Path(os.environ.get("WB_LIBRARY_SKILL", "")) if os.environ.get("WB_LIBRARY_SKILL") else None,
    Path.home() / "AppData/Local/Programs/WorkBuddy/resources/app.asar.unpacked/resources/plugins/workbuddy-builtin/skills/library",
    Path("/root/.codebuddy/skills/library"),
]


def log(msg: str) -> None:
    print(f"[daily_v2] {msg}", flush=True)


def find_skill() -> Path:
    for c in SKILL_CANDIDATES:
        if c and (c / "page" / "create_page_transaction.py").is_file():
            return c
    for root in (Path.home() / "AppData/Local/Programs/WorkBuddy", Path("/root")):
        for p in root.glob("**/skills/library/page/create_page_transaction.py"):
            return p.parent.parent
    raise SystemExit("找不到资料库 skill 目录，可用 WB_LIBRARY_SKILL 指定")


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)


def backfill_and_pick() -> str:
    """把面板从「最后一行的次日」补到今天（幂等），返回面板最新交易日。

    这样处理两种情况：正常日更只补 1 天；沙箱被清空后从仓库带来的 panel.csv
    会一次补齐中间所有缺失的工作日，无需人工干预。
    """
    import datetime as dt

    panel = HERE / "panel.csv"
    rows = list(csv.DictReader(panel.open(encoding="utf-8"))) if panel.exists() else []
    last = max((r["date"] for r in rows), default="2018-01-01")
    start = (dt.date.fromisoformat(last) + dt.timedelta(days=1)).isoformat()
    today = dt.date.today().isoformat()
    if start <= today:
        log(f"补采 {start} → {today}")
        run([sys.executable, str(HERE / "build_panel.py"), "--start", start, "--end", today,
             "--out", "panel.csv", "--workers", "6"])
    rows = list(csv.DictReader(panel.open(encoding="utf-8")))
    if not rows:
        raise SystemExit("面板为空，无法计算")
    return max(r["date"] for r in rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-git", action="store_true")
    a = ap.parse_args()

    target = a.date or backfill_and_pick()
    log(f"目标交易日 {target}")

    # 1-3 计算
    r = run([sys.executable, str(HERE / "daily_update_v2.py"), "--date", target,
             "--out", "latest_v2.json"])
    if r.returncode != 0:
        print(r.stdout, r.stderr)
        raise SystemExit("计算失败（可能是非交易日或取数异常）")
    log(r.stdout.strip().splitlines()[0])

    # 序列重算
    from search_scheme import build, load_panel
    rows = load_panel(HERE / "panel.csv")
    ser = build(rows, "roll1250", 250)
    with (HERE / "cb_series_v2.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["date", "cb", "bm_pct", "pb_pct"])
        w.writeheader()
        for x in ser:
            if x:
                w.writerow({k: (round(v, 6) if isinstance(v, float) else v) for k, v in x.items()})
    log(f"CB 序列 {sum(1 for x in ser if x)} 天")

    # 4 出页：优先以线上页为底
    skill = find_skill()
    base = HERE / "_live_base.html"
    try:
        out = run([sys.executable, str(skill / "page" / "download_page_artifacts.py"),
                   "--node-id", NODE_ID, "--list-only"])
        payload = out.stdout[out.stdout.find("{"):]
        j = json.loads(payload)
        if "artifacts" in j:
            entry = [x["path"].lstrip("/") for x in j["artifacts"] if x["path"].lower().endswith(".html")][0]
        else:  # CLI 输出形态：entry_html / files
            entry = j.get("entry_html") or (j.get("files") or [None])[0]
            if not entry:
                raise ValueError("找不到入口 HTML")
        url = urllib.parse.urljoin(j["url"], urllib.parse.quote(entry))
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}),
                                    timeout=60) as resp:
            base.write_bytes(resp.read())
        txt = base.read_text(encoding="utf-8")
        if not all(k in txt for k in ("便宜度分位（BM 中位数）", "触发测算", "五、历史信号")):
            raise ValueError("线上页结构不符")
        log(f"基底 = 线上当前页（{len(txt)} 字符）")
    except Exception as e:
        for cand in (HERE.parent / "bscore_patch" / "template_v23.html",
                     HERE.parent / "tools" / "template_v23.html"):
            if cand.is_file():
                base = cand
                break
        log(f"取线上页失败（{e}），回退模板")

    r = run([sys.executable, str(HERE / "patch_page_v2.py"), "--template", str(base),
             "--readings", str(HERE / "latest_v2.json"), "--series", str(HERE / "cb_series_v2.csv"),
             "--out", str(HERE / "page_v2.html")])
    print(r.stdout.strip())
    if r.returncode != 0:
        print(r.stderr)
        raise SystemExit("生成页面失败")

    if a.dry_run:
        log("dry-run：不写线上、不推仓库")
        return

    # 5 写线上
    out = run([sys.executable, str(skill / "page" / "create_page_transaction.py"), "--node-id", NODE_ID])
    tx = json.loads(out.stdout[out.stdout.find("{"):])
    txid = tx["transactionId"]
    work = HERE / "_apply"
    work.mkdir(exist_ok=True)
    dst = work / FILE_NAME
    shutil.copy(HERE / "page_v2.html", dst)
    r = run([sys.executable, str(skill / "page" / "upload_page_artifact.py"),
             "--transaction-id", txid, "--path", FILE_NAME, "--file", str(dst)])
    if r.returncode != 0:
        print(r.stdout, r.stderr)
        raise SystemExit("上传失败")
    r = run([sys.executable, str(skill / "page" / "commit_page_transaction.py"),
             "--transaction-id", txid, "--message", f"v2 日更：{target}",
             "--cleanup-dir", str(work)])
    m = re.search(r'"newVersion":(\d+)', r.stdout)
    log(f"已提交 v{m.group(1) if m else '?'}")
    r = run([sys.executable, str(skill / "page" / "publish_page.py"), "--node-id", NODE_ID])
    log("已同步发布态" if r.returncode == 0 else f"发布失败：{r.stderr[:200]}")

    # 6 状态入 git
    if not a.skip_git and (REPO / ".git").exists():
        dst_dir = REPO / "rebuild"
        dst_dir.mkdir(parents=True, exist_ok=True)
        for f in ("panel.csv", "cb_series_v2.csv", "latest_v2.json"):
            shutil.copy(HERE / f, dst_dir / f)
        run(["git", "-C", str(REPO), "add", "rebuild/panel.csv", "rebuild/cb_series_v2.csv",
             "rebuild/latest_v2.json"])
        r = run(["git", "-C", str(REPO), "-c", "user.name=mathskit-pure",
                 "-c", "user.email=mathskit-pure@users.noreply.github.com",
                 "commit", "-q", "-m", f"data(v2): {target} 日更（面板/序列/读数）"])
        ok = r.returncode == 0
        if ok:
            env = dict(os.environ, GIT_SSH_COMMAND="ssh -i ~/.ssh/id_ed25519_afgi -o IdentitiesOnly=yes")
            rr = run(["git", "-C", str(REPO), "push", "-q", "origin", "main"], env=env)
            log("状态已推送 GitHub" if rr.returncode == 0 else f"推送失败：{rr.stderr[:150]}")
        else:
            log("无变化，未提交")


if __name__ == "__main__":
    main()

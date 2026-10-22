#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""read_diag.py —— 把「云端体检」诊断页取下来，打印正文与所有链接。

用途：云端任务跑完后，诊断页里写着各备份压缩包的名称、体积和资料库链接。
本脚本把页面内容读成可读文本，并单独列出所有链接，方便下一步下载。

用法：
    python3 read_diag.py                 # 默认读 3oMvrR1KyOVhuEd0YbheFL
    python3 read_diag.py --node <id>
    python3 read_diag.py --save out.html # 同时存原始 HTML
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_NODE = "3oMvrR1KyOVhuEd0YbheFL"


def find_library_skill() -> Path:
    env = os.environ.get("WB_LIBRARY_SKILL")
    cands = []
    if env:
        cands.append(Path(env))
    home = Path.home()
    cands += [
        home / "AppData/Local/Programs/WorkBuddy/resources/app.asar.unpacked/resources/plugins/workbuddy-builtin/skills/library",
        Path("/root/.codebuddy/skills/library"),
        home / ".codebuddy/skills/library",
    ]
    for c in cands:
        if (c / "space_api.py").is_file():
            return c
    for root in (home / "AppData/Local/Programs/WorkBuddy", Path("/root")):
        for p in root.glob("**/skills/library/space_api.py"):
            return p.parent
    raise SystemExit("找不到 library skill 目录，可用 WB_LIBRARY_SKILL 指定")


def fetch_page(node_id: str):
    sys.path.insert(0, str(find_library_skill()))
    from _common import http_request, unwrap_data, USER_AGENT  # type: ignore

    d = unwrap_data(http_request("POST", "/space/api/agent/v1/list-page-artifacts",
                                 body={"nodeId": node_id}, timeout=25.0))
    entries = [a["path"].lstrip("/") for a in d["artifacts"] if a["path"].lower().endswith((".html", ".htm"))]
    if not entries:
        raise SystemExit("该节点没有 HTML 产物")
    url = urllib.parse.urljoin(d["url"], urllib.parse.quote(entries[0]))
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", "replace"), d.get("version"), r.headers.get("Last-Modified")


def to_text(html: str) -> str:
    s = re.sub(r"<(script|style)[\s\S]*?</\1>", "", html)
    s = re.sub(r"<!--[\s\S]*?-->", "", s)
    s = re.sub(r"</(tr|div|p|h\d|li)>", "\n", s)
    s = re.sub(r"</t[dh]>", " | ", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    lines = [re.sub(r"[ \t]+", " ", ln).strip(" |") for ln in s.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--node", default=DEFAULT_NODE)
    ap.add_argument("--save", default="")
    a = ap.parse_args()

    html, ver, lm = fetch_page(a.node)
    print(f"版本 v{ver}　Last-Modified {lm}　{len(html)} 字节\n")
    if a.save:
        Path(a.save).write_bytes(html.encode("utf-8"))
        print(f"原始 HTML 已存：{a.save}\n")

    print("=" * 60)
    print(to_text(html))
    print("=" * 60)

    links = sorted(set(re.findall(r'href="([^"]+)"', html)))
    if links:
        print("\n页面内链接：")
        for ln in links:
            print("  ", ln)


if __name__ == "__main__":
    main()

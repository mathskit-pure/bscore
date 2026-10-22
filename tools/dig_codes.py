#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dig_codes.py —— 从会话记录（.jsonl）里回收曾经写过的源码。

背景：云端 /workspace 被重置，bscore / bscombo 代码在云上和本机都没有副本。
但历史上那些文件是通过会话写出去的，写出的内容会留在会话记录里。
本脚本扫描 .workbuddy/projects 下所有 jsonl，找出「像 Python 源码」的长字符串，
按关键特征打分后落盘，供人工确认。

用法：
    python3 dig_codes.py --out ./_dig
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path.home() / ".workbuddy" / "projects"

# 某个文件「应该是」什么，用特征串判断
SIGNATURES = {
    "daily_update": ["cache_universe", "latest.json", "dashboard.html"],
    "update_bscombo": ["read_dashboard_cb", "build_blocks", "bscombo"],
    "patch_page": ["page_base.html", "pool_page"],
}

PY_HINT = re.compile(r"^(import|from|def|class|#|@|\s*\"\"\")", re.M)


def looks_like_python(s: str) -> bool:
    if len(s) < 2000:
        return False
    if s.count("\n") < 40:
        return False
    hits = len(PY_HINT.findall(s))
    return hits >= 20


def walk_strings(obj, depth=0):
    """把 JSON 里所有字符串值都吐出来（含嵌套）。"""
    if depth > 12:
        return
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from walk_strings(v, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk_strings(v, depth + 1)


def unescape(s: str) -> str:
    """还原反斜杠转义。用 JSON 解码而不是 unicode_escape ——
    后者会把 UTF-8 中文按 latin-1 拆碎，产出无法阅读的乱码。"""
    if "\\n" in s and "\n" not in s:
        try:
            return json.loads('"' + s.replace('"', '\\"') + '"')
        except Exception:
            return s.replace("\\n", "\n").replace("\\t", "\t")
    return s


def unwrap_jsonish(t: str) -> str:
    """有些记录是「双重编码」的：字符串值本身又是一段 {"content": "..."} 的 JSON。

    先尝试整段 json.loads 取 content；失败（被截断）就剥掉外壳再按 JSON 字符串解码。
    """
    for _ in range(3):
        st = t.strip()
        if not (st.startswith("{") and '"content"' in st[:200]):
            break
        try:
            o = json.loads(st)
            if isinstance(o, dict) and isinstance(o.get("content"), str):
                t = o["content"]
                continue
        except Exception:
            pass
        # 截断兜底：剥壳 + 解码转义
        m = re.match(r'^\{\s*"content"\s*:\s*"([\s\S]*)$', st)
        if m:
            body = m.group(1)
            if body.endswith('"}'):
                body = body[:-2]
            try:
                t = json.loads('"' + body + '"')
                continue
            except Exception:
                break
        break
    return t


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="./_dig")
    ap.add_argument("--min-score", type=int, default=2)
    a = ap.parse_args()

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    found = {}  # (kind, sha1) -> (score, text, source)
    files = sorted(ROOT.rglob("*.jsonl"))
    print(f"扫描 {len(files)} 个会话记录……")

    for f in files:
        try:
            raw = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for line in raw.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            for s in walk_strings(obj):
                t = unwrap_jsonish(unescape(s))
                if not looks_like_python(t):
                    continue
                for kind, sigs in SIGNATURES.items():
                    score = sum(1 for k in sigs if k in t)
                    if score < a.min_score:
                        continue
                    key = (kind, hash(t))
                    if key in found:
                        continue
                    found[key] = (score, t, f.name)

    print(f"命中候选 {len(found)} 段\n")
    rows = []
    for (kind, _), (score, text, src) in found.items():
        dst = out / f"{kind}_score{score}_{len(text)}.py"
        i = 1
        while dst.exists():
            dst = out / f"{kind}_score{score}_{len(text)}_{i}.py"
            i += 1
        dst.write_text(text, encoding="utf-8")
        rows.append((kind, score, len(text), dst.name, src))

    rows.sort(key=lambda r: (-r[1], -r[2]))
    for kind, score, size, name, src in rows:
        print(f"{kind:16} score={score} size={size:>7}  -> {name}   (来自 {src})")
    print(f"\n已落盘到 {out.resolve()}")


if __name__ == "__main__":
    main()

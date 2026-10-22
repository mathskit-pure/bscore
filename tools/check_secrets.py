#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_secrets.py —— 推送公开仓库前的凭证扫描。

用于把云端备份的代码推到 GitHub 公开仓库之前，扫一遍可能泄露的凭据。
只读扫描，不改任何文件。

退出码：0 = 未发现高危项（可能有低危提示）；1 = 发现高危项，应处理后才能推送。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".ipynb_checkpoints", "_backup", "_run"}
SKIP_EXT = {".pyc", ".pyo", ".png", ".jpg", ".jpeg", ".gif", ".zip", ".gz", ".tar", ".parquet", ".pkl"}

# (严重级别, 名称, 正则)
PATTERNS = [
    ("HIGH", "GitHub token", re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}")),
    ("HIGH", "GitHub PAT", re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
    ("HIGH", "OpenAI/Anthropic key", re.compile(r"\bsk-[A-Za-z0-9\-_]{20,}")),
    ("HIGH", "AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("HIGH", "私钥文件内容", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("HIGH", "疑似硬编码密码/密钥",
     re.compile(r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|appsecret)\b"
                r"\s*[:=]\s*[\"'][^\"'\s]{8,}[\"']")),
    ("HIGH", "Cookie / SessionID 赋值",
     re.compile(r"(?i)\b(cookie|sessionid|session_id|sid)\b\s*[:=]\s*[\"'][^\"'\s]{12,}[\"']")),
    ("LOW", "疑似账户/邮箱", re.compile(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}")),
    ("LOW", "疑似手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
]


def iter_files(root: Path):
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.suffix.lower() in SKIP_EXT:
            continue
        yield p


def scan(root: Path, max_bytes: int = 4_000_000):
    findings = []
    for p in iter_files(root):
        try:
            if p.stat().st_size > max_bytes:
                continue
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if len(line) > 4000:
                continue
            for level, name, rx in PATTERNS:
                for m in rx.finditer(line):
                    snippet = m.group(0)
                    if len(snippet) > 60:
                        snippet = snippet[:24] + "…" + snippet[-8:]
                    findings.append((level, name, p, lineno, snippet, line.strip()[:110]))
    return findings


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--show-low", action="store_true", help="同时列出低危提示（邮箱/手机号）")
    a = ap.parse_args()

    root = Path(a.root).resolve()
    if not root.is_dir():
        sys.exit(f"目录不存在：{root}")

    # 高危后缀文件直接点名
    warn_files = [p for p in iter_files(root) if p.suffix.lower() in {".pem", ".key", ".p12", ".pfx"}
                  or p.name in {"id_rsa", "id_ed25519", ".env", "credentials.json", "token.json"}]

    fs = scan(root)
    high = [f for f in fs if f[0] == "HIGH"]
    low = [f for f in fs if f[0] == "LOW"]

    print(f"扫描目录：{root}")
    print(f"高危命中：{len(high)}　低危提示：{len(low)}　敏感后缀文件：{len(warn_files)}\n")

    for level, name, p, lineno, snip, ctx in high:
        print(f"[{level}] {name}  {p.relative_to(root)}:{lineno}")
        print(f"        命中：{snip}")
        print(f"        原文：{ctx}\n")

    for p in warn_files:
        print(f"[HIGH] 敏感文件  {p.relative_to(root)}")

    if a.show_low:
        for level, name, p, lineno, snip, ctx in low:
            print(f"[{level}] {name}  {p.relative_to(root)}:{lineno}  {snip}")

    if high or warn_files:
        print("\n结论：发现高危项，处理后再推送。")
        sys.exit(1)
    print("结论：未发现高危凭据，可以推送。")


if __name__ == "__main__":
    main()

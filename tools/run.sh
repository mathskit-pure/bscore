#!/usr/bin/env bash
# 用五章式模板增量刷新 B-score 看板（替代 import_html 整体覆写）。
#
# 用法：bash /workspace/pool-core/cloud/bscore_patch/run.sh
# 退出码：0 = 已刷新或页面无需变化；3 = 安全跳过（不写页面）；1 = 环境异常；2 = 补丁失败
set -uo pipefail

NODE_ID="VLeP5CSpUchSY3qawGq88W"
FILE_NAME="A股B-score择时信号看板.html"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK="${HERE}/_run"
mkdir -p "$WORK"

log() { printf '[patch_v23] %s\n' "$*"; }

# ---------- 1. 定位资料库 skill 脚本 ----------
find_skill() {
  local name="$1" p
  for p in "/root/.codebuddy/skills/library/page/$name" \
           "/root/.codebuddy/skills/library/drive/$name" \
           "$HOME/.codebuddy/skills/library/page/$name"; do
    [ -f "$p" ] && { printf '%s' "$p"; return; }
  done
  # 兜底：只在 /root 与 /home 里找，跳过 /workspace（那里有几十 MB 的缓存，扫起来慢）
  timeout 20 find /root /home -name "$name" -path "*library*" 2>/dev/null | head -1
}
IMPORT_HTML="$(find_skill import_html.py)"
DOWNLOAD="$(find_skill download_page_artifacts.py)"

# ---------- 2. 基线：优先用线上当前页，失败则退回内置模板 ----------
BASE="${HERE}/template_v23.html"
if [ -n "$DOWNLOAD" ]; then
  if python3 "$DOWNLOAD" --node-id "$NODE_ID" --list-only >"$WORK/list.json" 2>"$WORK/list.err"; then
    python3 - "$WORK/list.json" "$WORK/url.txt" "$WORK/name.txt" <<'PY' || true
import json, sys, urllib.parse
raw = open(sys.argv[1], encoding="utf-8").read()
raw = raw[raw.find("{"):] if "{" in raw else raw
j = json.loads(raw)
html = [a for a in j.get("artifacts", []) if (a.get("path") or "").lower().endswith((".html", ".htm"))]
open(sys.argv[3], "w", encoding="utf-8").write(html[0]["path"].lstrip("/"))
base = j["url"]
base = base if base.endswith("/") else base + "/"
open(sys.argv[2], "w", encoding="utf-8").write(base)
PY
    if [ -s "$WORK/url.txt" ] && [ -s "$WORK/name.txt" ]; then
      URL="$(cat "$WORK/url.txt")$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1]))" "$(cat "$WORK/name.txt")")"
      if curl -fsSL --max-time 60 "$URL" -o "$WORK/base.html" && [ -s "$WORK/base.html" ]; then
        # 只有确认是五章式结构才拿它当基线，否则用内置模板
        if python3 - "$WORK/base.html" <<'PY'
import sys
s = open(sys.argv[1], encoding="utf-8").read()
need = ["便宜度分位（BM 中位数）", "双便宜均值 CB", "触发测算", "四、历史走势", "五、历史信号"]
sys.exit(0 if all(n in s for n in need) else 1)
PY
        then
          BASE="$WORK/base.html"
          log "基线 = 线上当前页（五章式，$(stat -c%s "$BASE") 字节）"
        else
          log "线上页非五章式结构，改用内置模板"
        fi
      fi
    fi
  else
    log "线上页下载失败，改用内置模板：$(head -c 200 "$WORK/list.err" 2>/dev/null)"
  fi
fi

# ---------- 3. 数据源 ----------
LATEST="${BSCORE_LATEST:-/workspace/bscore/data/latest.json}"
HIST="${BSCORE_HIST:-/workspace/bscore/data/cb_history.csv}"
[ -f "$LATEST" ] || { log "缺少 $LATEST，不刷新页面"; exit 3; }

# 月频 CB 序列（用于重绘第四章）：按候选名探测，找不到就保留原图
CHART_CSV=""
for c in /workspace/bscore/data/cb_monthly.csv /workspace/bscore/data/cb_history_monthly.csv \
         /workspace/bscore/data/cb_monthly_series.csv /workspace/bscombo/data/cb_monthly.csv; do
  [ -f "$c" ] && CHART_CSV="$c" && break
done

# ---------- 4. 打补丁（fail-closed） ----------
ARGS=(--template "$BASE" --latest "$LATEST" --out "$WORK/out.html" --json-out "$WORK/report.json")
[ -f "$HIST" ] && ARGS+=(--history "$HIST")
[ -n "$CHART_CSV" ] && ARGS+=(--chart-csv "$CHART_CSV")

python3 "${HERE}/patch_v23.py" "${ARGS[@]}"
RC=$?
if [ $RC -ne 0 ]; then
  log "补丁失败（退出码 $RC），页面保持不动；报告：$WORK/report.json"
  exit 2
fi

# ---------- 5. 写回页面 ----------
if [ "${PATCH_DRY:-0}" = "1" ]; then
  log "PATCH_DRY=1，跳过写回；产物 $WORK/out.html（$(stat -c%s "$WORK/out.html") 字节）"
  exit 0
fi
if [ -z "$IMPORT_HTML" ]; then
  log "找不到 import_html.py，无法写回；已生成 $WORK/out.html"
  exit 1
fi
python3 "$IMPORT_HTML" "$WORK/out.html" --file-name "$FILE_NAME" --node-block-id "$NODE_ID"
RC=$?
if [ $RC -ne 0 ]; then
  log "写回失败（退出码 $RC）"
  exit 1
fi
log "已写回 $NODE_ID；图表状态：$(python3 -c "import json;print(json.load(open('$WORK/report.json',encoding='utf-8')).get('chart'))" 2>/dev/null)"
exit 0

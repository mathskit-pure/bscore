#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""patch_v23.py —— 把 B-score 每日最新读数增量刷新到「五章式」看板模板里。

用途：替代 import_html.py 的整体覆写。只改模板中的动态读数，
章节结构、文案、样式、pnid 全部原样保留。

用法：
    python3 patch_v23.py --template template_v23.html \
                         --latest latest.json \
                         --out patched.html \
                         [--chart chart.png] [--json-out report.json]

设计要点：
· 锚定在「行标签文字」上，不依赖 td 顺序以外的结构假设；
· latest.json 的键名未知，用候选键 + 归一化模糊匹配递归查找；
· 任一项解析不到、或某个锚点没命中 → 退出码 2，不产出文件（fail-closed）；
· 只允许四类区域变动：指标表 6 行、指数表 3 行、触发测算段、走势图。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

FIELD_KEYS = {
    "date":         ["trade_date", "date", "数据交易日", "trading_date", "asof"],
    "bm_cur":       ["bm_median", "bm_pct", "bm_percentile", "cheap_bm", "便宜度分位"],
    "bm_hist":      ["bm_hist_pct", "bm_percentile_hist", "bm_hist"],
    "pb_value":     ["pb", "pb_value", "market_pb", "全市场PB"],
    "pb_pct":       ["pb_pct", "pb_percentile", "pb_quantile", "pb_cheap_pct", "全市场PB分位"],
    "cb":           ["cb", "cb_value", "cb_mean", "双便宜均值"],
    "broken_pct":   ["broken_net", "broken_pct", "broken_net_ratio", "破净率"],
    "weighted_pb":  ["weighted_pb", "pb_weighted", "pb_snapshot", "全市场加权PB"],
    "sample_n":     ["sample_n", "sample_count", "n_stocks", "universe_n", "有效样本"],
    "hs300_close":  ["hs300_close", "hs300", "index_300_close", "沪深300"],
    "hs300_chg":    ["hs300_chg", "hs300_pct_chg"],
    "hs300_dd":     ["hs300_dd", "hs300_drawdown", "hs300_dd_250"],
    "zz500_close":  ["zz500_close", "zz500", "index_500_close", "中证500"],
    "zz500_chg":    ["zz500_chg", "zz500_pct_chg"],
    "zz500_dd":     ["zz500_dd", "zz500_drawdown"],
    "zz1000_close": ["zz1000_close", "zz1000", "csi1000", "index_1000_close", "中证1000"],
    "zz1000_chg":   ["zz1000_chg", "zz1000_pct_chg", "csi1000_chg"],
    "zz1000_dd":    ["zz1000_dd", "zz1000_drawdown", "csi1000_dd"],
    "trig_pb":      ["trigger_pb", "pb_trigger", "target_pb", "建仓触发PB"],
    "trig_bm":      ["trigger_bm", "bm_trigger", "target_bm", "建仓触发BM"],
    "trig_hs300":   ["trigger_hs300", "hs300_trigger", "target_hs300", "建仓触发沪深300"],
}

PCTILE_FIELDS = {"bm_cur", "bm_hist", "pb_pct", "cb", "trig_bm"}


def norm(k) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", str(k).lower())


def flatten(obj, prefix="", segs: tuple = ()):
    """展开成 {(归一化键, 路径): 值}。

    除了叶子键本身，还登记「父键+子键」的组合键（深度 2、3），
    这样 latest.json 里 {"hs300": {"chg": 0.12}} 这种嵌套也能被 hs300_chg 命中。
    """
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{prefix}/{k}"
            s = segs + (norm(k),)
            if isinstance(v, (dict, list)):
                out.update(flatten(v, p, s))
                continue
            out[(s[-1], p)] = v
            if len(s) >= 2:
                out[("".join(s[-2:]), p)] = v
            if len(s) >= 3:
                out[("".join(s[-3:]), p)] = v
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.update(flatten(v, f"{prefix}[{i}]", segs))
    return out


def resolve(latest, field):
    flat = flatten(latest)
    cands = [norm(c) for c in FIELD_KEYS[field]]
    for c in cands:
        hits = [(p, v) for (k, p), v in flat.items() if k == c and not isinstance(v, (dict, list))]
        if hits:
            return hits[0][1], hits[0][0]
    for c in cands:
        if len(c) < 2:
            continue
        hits = [(p, v) for (k, p), v in flat.items() if c in k and not isinstance(v, (dict, list))]
        if hits:
            return hits[0][1], hits[0][0]
    return None, None


def as_float(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip().replace(",", "").replace("%", ""))
        except ValueError:
            return None
    return None


def pctile(v: float) -> float:
    """百分位统一到 0~1。"""
    return v / 100.0 if v > 1.5 else v


# 源数据里「涨跌幅/回撤/破净率」是百分数数值（-17.6 即 -17.6%）。
# 若某个数据源给的是小数形式（0.0012），用 --percent-scale 100 让它乘回去。
PERCENT_SCALE = {"k": 1.0}


def as_percent(v: float) -> float:
    return v * PERCENT_SCALE["k"]


def fmt(field: str, v: float) -> str:
    if field in PCTILE_FIELDS:
        return f"{pctile(v):.3f}"
    if field in ("pb_value", "weighted_pb", "trig_pb"):
        return f"{v:.2f}"
    if field in ("hs300_close", "zz500_close", "zz1000_close", "trig_hs300"):
        return f"{v:.0f}"
    if field in ("hs300_chg", "zz500_chg", "zz1000_chg"):
        return f"{as_percent(v):+.1f}%"
    if field in ("hs300_dd", "zz500_dd", "zz1000_dd", "broken_pct"):
        return f"{as_percent(v):.1f}%"
    if field == "sample_n":
        return f"{int(round(v))} 只"
    return f"{v}"


TD_RE = re.compile(r"<td([^>]*)>(.*?)</td>", re.S)


COMMENT_RE = re.compile(r"<!--pnid:[^>]*?-->")


def rebuild_cell(inner: str, new_text: str) -> str:
    """保留单元格里的 pnid 注释与 <b> 包裹，只换文字。"""
    comments = "".join(COMMENT_RE.findall(inner))
    bm = re.search(r"<b([^>]*)>", inner)
    if bm:
        return f"<b{bm.group(1)}>{comments}{new_text}</b>"
    return f"{comments}{new_text}"


def _rewrite_cells(row: str, mapping: dict) -> str:
    idx, pieces, last = 0, [], 0
    for tm in TD_RE.finditer(row):
        idx += 1
        pieces.append(row[last:tm.start()])
        if idx in mapping:
            pieces.append(f"<td{tm.group(1)}>{rebuild_cell(tm.group(2), mapping[idx])}</td>")
        else:
            pieces.append(tm.group(0))
        last = tm.end()
    pieces.append(row[last:])
    return "".join(pieces)


def patch_metric_row(html: str, label: str, cells: dict):
    pat = re.compile(r"<tr[^>]*>\s*<td[^>]*>(?:(?!</tr>).)*?" + re.escape(label) + r"(?:(?!</tr>).)*?</tr>", re.S)
    m = pat.search(html)
    if not m:
        return html, False
    return html[:m.start()] + _rewrite_cells(m.group(0), cells) + html[m.end():], True


def patch_index_row(html: str, name: str, close: str, chg: str, dd: str):
    # 用「整行内出现指数名」来定位：单元格里夹杂 <!--pnid:xxx--> 注释，
    # 不能假设名字紧跟 <td>。
    pat = re.compile(r"<tr[^>]*>(?:(?!</tr>).)*?" + re.escape(name) + r"(?:(?!</tr>).)*?</tr>", re.S)
    m = pat.search(html)
    if not m:
        return html, False
    row = m.group(0)
    dd_num = float(dd.replace("%", ""))
    color = "#c0392b" if dd_num > 0 else "#27ae60"
    idx, pieces, last = 0, [], 0
    for tm in TD_RE.finditer(row):
        idx += 1
        pieces.append(row[last:tm.start()])
        if idx == 3:
            pieces.append(f"<td{tm.group(1)}>{rebuild_cell(tm.group(2), close)}</td>")
        elif idx == 4:
            pieces.append(f"<td{tm.group(1)}>{rebuild_cell(tm.group(2), chg)}</td>")
        elif idx == 5:
            attrs = re.sub(r'\s*style="[^"]*"', "", tm.group(1))
            pieces.append(f'<td{attrs} style="color:{color}">{rebuild_cell(tm.group(2), dd)}</td>')
        else:
            pieces.append(tm.group(0))
        last = tm.end()
    pieces.append(row[last:])
    return html[:m.start()] + "".join(pieces) + html[m.end():], True


def patch_trigger(html: str, vals: dict):
    i = html.find("触发测算")
    if i < 0:
        return html, False
    j = html.find("</p>", i)
    if j < 0:
        return html, False
    seg = html[i:j]
    orig = seg

    bolds = list(re.finditer(r"<b([^>]*)>(.*?)</b>", seg, re.S))
    if len(bolds) < 3:
        return html, False
    targets = [vals["trig_pb"], vals["trig_bm"], vals["trig_hs300"]]
    for k in (2, 1, 0):
        b = bolds[k]
        keep = "".join(COMMENT_RE.findall(b.group(2)))
        seg = seg[:b.start(2)] + keep + targets[k] + seg[b.end(2):]

    # 三个「（当前 X）」，顺序：全市场 PB、BM 中位数、沪深300
    news = [vals["pb_value"], vals["bm_cur"], vals["hs300_close"]]
    counter = {"i": 0}

    def sub(mm):
        k = counter["i"]
        counter["i"] += 1
        return mm.group(1) + news[k] if k < len(news) else mm.group(0)

    seg = re.sub(r"(（当前\s*)[\d.,]+", sub, seg)
    # 三个目标值与三个「当前值」都必须命中；值恰好未变也算通过
    if counter["i"] < 3:
        return html, False
    return html[:i] + seg + html[j:], True


RANGES = {
    # field: (下限, 上限, 是否含端点)
    "bm_cur": (0.0, 1.0), "bm_hist": (0.0, 1.0), "pb_pct": (0.0, 1.0),
    "cb": (0.0, 1.0), "trig_bm": (0.0, 1.0),
    "pb_value": (0.01, 50.0), "weighted_pb": (0.01, 50.0), "trig_pb": (0.01, 50.0),
    "broken_pct": (0.0, 100.0),
    "hs300_close": (100.0, 100000.0), "zz500_close": (100.0, 100000.0), "zz1000_close": (100.0, 100000.0),
    "trig_hs300": (100.0, 100000.0),
    "hs300_chg": (-100.0, 100.0), "zz500_chg": (-100.0, 100.0), "zz1000_chg": (-100.0, 100.0),
    "hs300_dd": (-100.0, 100.0), "zz500_dd": (-100.0, 100.0), "zz1000_dd": (-100.0, 100.0),
    "sample_n": (100.0, 20000.0),
}


def check_range(field: str, v: float):
    """越界返回错误描述；用于拦住「取错字段」这类静默错误。"""
    lo, hi = RANGES.get(field, (None, None))
    if lo is None:
        return None
    # 百分位型字段允许被写成 0~100 的百分数，pctile() 会统一回 0~1
    if field in PCTILE_FIELDS and 1.5 < v <= 100.0:
        return None
    if not (lo <= v <= hi):
        return f"{field}={v} 不在 [{lo}, {hi}]"
    return None


def read_last_csv_row(path: str):
    """读 cb_history.csv 最后一行，作为 latest.json 缺字段时的兜底源。

    该文件的表头由云端 daily_update.py 决定（我这边不控制），所以不做列名假设，
    原样返回 dict，交给 resolve() 的归一化匹配去认。
    """
    p = Path(path)
    if not p.is_file():
        return {}, None
    import csv
    txt = p.read_bytes().decode("utf-8-sig", "replace")
    rows = list(csv.DictReader(txt.splitlines()))
    if not rows:
        return {}, None
    last = {k: v for k, v in rows[-1].items() if k is not None}
    return last, len(rows)


def dump_keys(obj, prefix="", out=None, limit=200):
    """把 JSON 的叶���路径全量列出来，供首次对接核对真实键名。"""
    out = out if out is not None else []
    if len(out) >= limit:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            dump_keys(v, f"{prefix}.{k}" if prefix else k, out, limit)
    elif isinstance(obj, list):
        out.append(f"{prefix}[] (list, n={len(obj)})")
    else:
        out.append(f"{prefix} = {obj!r}")
    return out


def make_chart(series, out_png: str):
    """按 v23 原图样式重绘 CB 月频走势（标题/阈值线/配色对齐）。"""
    import datetime as _dt
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    xs = [_dt.datetime.strptime(d, "%Y-%m-%d") if len(d) >= 10 else _dt.datetime(int(d[:4]), 1, 1) for d, _ in series]
    ys = [v for _, v in series]
    fig, ax = plt.subplots(figsize=(11.0, 3.6), dpi=110)
    ax.plot(xs, ys, color="#c0392b", lw=1.4, label="CB (double-cheap mean)")
    ax.axhline(0.80, ls="--", lw=1.2, color="#2a9d8f", label="0.80 full position")
    ax.axhline(0.75, ls="--", lw=1.2, color="#e07b39", label="0.75 build position")
    ax.axhline(0.50, ls=":", lw=1.0, color="#999999")
    ax.set_ylim(0, 1.0)
    ax.set_ylabel("CB")
    ax.set_title("B-score CB history (monthly, expanding percentile since 2005)", fontsize=10)
    ax.grid(True, ls=":", lw=0.6, color="#dddddd")
    ax.legend(loc="upper left", fontsize=7.5, ncol=3, frameon=True, framealpha=0.9)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.tight_layout()
    fig.savefig(out_png, format="png")
    plt.close(fig)


def load_chart_csv(path: str):
    """读月频 CB 序列（列名宽松识别：日期列 + CB 列），返回 [(YYYY-MM-DD, cb)]。"""
    import csv
    p = Path(path)
    if not p.is_file():
        return []
    txt = p.read_bytes().decode("utf-8-sig", "replace")
    rows = list(csv.DictReader(txt.splitlines()))
    if not rows:
        return []
    keys = list(rows[0].keys())
    dk = next((k for k in keys if re.search(r"date|日期|month|月份|trade", norm(k))), None)
    ck = next((k for k in keys if k != dk and re.search(r"^(cb|双便宜|cbmean|cbvalue)", norm(k))), None)
    if not dk or not ck:
        return []
    out = []
    for r in rows:
        d, v = str(r.get(dk) or "")[:10], as_float(r.get(ck))
        if len(d) >= 7 and v is not None:
            if len(d) == 7:
                d += "-01"
            out.append((d, v))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", required=True)
    ap.add_argument("--latest", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--history", default="", help="cb_history.csv，latest 缺字段时兜底")
    ap.add_argument("--chart", default="")
    ap.add_argument("--chart-csv", default="", help="月频 CB 序列 csv；提供则重绘第四章走势图")
    ap.add_argument("--json-out", default="")
    ap.add_argument("--probe", action="store_true", help="只导出 latest.json / cb_history.csv 的真实结构与解析结果，不改页面")
    ap.add_argument("--percent-scale", default="1", choices=["1", "100"],
                    help="源数据的涨跌幅/回撤/破净率是百分数(1)还是小数(100→×100)")
    a = ap.parse_args()

    PERCENT_SCALE["k"] = float(a.percent_scale)
    tpl = Path(a.template).read_bytes().decode("utf-8")
    latest = json.loads(Path(a.latest).read_bytes().decode("utf-8"))
    hist_row, hist_n = ({}, None) if not a.history else read_last_csv_row(a.history)

    vals, prov, missing, src, out_of_range = {}, {}, [], {}, []
    for f in FIELD_KEYS:
        raw, path = resolve(latest, f)
        origin = "latest.json"
        if raw is None or (f != "date" and as_float(raw) is None):
            if hist_row:
                raw2, path2 = resolve(hist_row, f)
                if raw2 is not None and (f == "date" or as_float(raw2) is not None):
                    raw, path, origin = raw2, path2, "cb_history.csv"
        if f == "date":
            if raw is None:
                missing.append(f)
            else:
                vals[f] = str(raw)
                prov[f] = {"value": raw, "key": path, "from": origin}
                src[f] = origin
            continue
        num = as_float(raw)
        if num is None:
            missing.append(f)
            continue
        bad = check_range(f, num)
        if bad:
            out_of_range.append(bad)
            continue
        vals[f] = fmt(f, num)
        prov[f] = {"value": raw, "key": path, "from": origin}
        src[f] = origin

    if a.probe:
        rep = {
            "ok": True, "mode": "probe",
            "latest_keys": dump_keys(latest),
            "history_header": list(hist_row.keys()) if hist_row else None,
            "history_rows": hist_n,
            "history_last_row": hist_row,
            "resolved": prov,
            "missing": missing,
            "chart_csv_rows": len(load_chart_csv(a.chart_csv)) if a.chart_csv else 0,
        }
        if a.json_out:
            Path(a.json_out).write_bytes(json.dumps(rep, ensure_ascii=False, indent=1).encode("utf-8"))
        print(json.dumps({"ok": True, "mode": "probe", "missing": missing,
                          "resolved_from": src}, ensure_ascii=False))
        return

    if missing or out_of_range:
        print(json.dumps({"ok": False, "reason": "字段未解析到或数值越界",
                          "missing": missing, "out_of_range": out_of_range},
                         ensure_ascii=False), file=sys.stderr)
        sys.exit(2)

    html, done = tpl, {}
    for label, cells in [
        ("便宜度分位（BM 中位数）", {2: vals["bm_cur"], 3: vals["bm_hist"]}),
        ("PB 便宜分位（全市场 PB）", {2: vals["pb_value"], 3: vals["pb_pct"]}),
        ("双便宜均值 CB", {3: vals["cb"]}),
        ("破净股占比", {2: vals["broken_pct"]}),
        ("全市场加权 PB（快照口径）", {2: vals["weighted_pb"]}),
        ("有效样本", {2: vals["sample_n"]}),
    ]:
        html, done[label] = patch_metric_row(html, label, cells)

    for name, key in [("沪深300", "hs300"), ("中证500", "zz500"), ("中证1000", "zz1000")]:
        html, done[name] = patch_index_row(html, name, vals[f"{key}_close"], vals[f"{key}_chg"], vals[f"{key}_dd"])

    html, done["触发测算"] = patch_trigger(html, vals)

    chart_note = "kept"
    chart_png = a.chart
    if not chart_png and a.chart_csv:
        series = load_chart_csv(a.chart_csv)
        if len(series) >= 24:
            chart_png = str(Path(a.out).with_name("_chart.png"))
            try:
                make_chart(series, chart_png)
                chart_note = f"redrawn({len(series)} pts)"
            except Exception as e:  # 画图失败不该让整个刷新失败
                chart_note = f"draw-failed: {e}"
                chart_png = ""
        else:
            chart_note = f"skip(series={len(series)})"

    if chart_png and Path(chart_png).is_file():
        import base64
        b64 = base64.b64encode(Path(chart_png).read_bytes()).decode()
        html, n = re.subn(r'(src="data:image/png;base64,)[^"]*(")', lambda m: m.group(1) + b64 + m.group(2), html, count=1)
        done["走势图"] = n == 1
    else:
        done["走势图"] = True  # 无新月频源时保留原图，不算失败

    # 数据既然刷新成功，就顺手摘掉「本页数据已过期」横幅（由 stale_banner.py 插入）
    if "<!--STALE_BANNER:start-->" in html:
        html, n = re.subn(r"<!--STALE_BANNER:start-->.*?<!--STALE_BANNER:end-->\s*", "", html, flags=re.S)
        done["清除过期横幅"] = n >= 1

    failed = [k for k, v in done.items() if not v]
    if failed:
        print(json.dumps({"ok": False, "reason": "锚点未命中", "failed": failed}, ensure_ascii=False), file=sys.stderr)
        sys.exit(2)

    Path(a.out).write_bytes(html.encode("utf-8"))
    if a.json_out:
        Path(a.json_out).write_bytes(json.dumps({"ok": True, "values": prov, "done": done, "date": vals["date"],
                                                 "chart": chart_note, "from": src},
                                                ensure_ascii=False, indent=1).encode("utf-8"))
    print(json.dumps({"ok": True, "out": a.out, "date": vals["date"], "chart": chart_note}, ensure_ascii=False))


if __name__ == "__main__":
    main()

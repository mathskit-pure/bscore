# bscore

A股 B-score 择时信号（双便宜均值 CB）相关的代码、工具与口径存档。

## 这个仓库为什么存在

2026-10-22 发现云端工作区 `/workspace` 被**整体重置**，`bscore` / `bscombo` / `pool-core` /
`prof-model` / `mom_opt` 五个目录全部消失，起因是云端积分额度耗尽后沙箱被销毁。

其中 `pool-core`、`prof-model`、`mom_opt`、`afgi_tdx` 在本机与 GitHub 都有完整副本，
可以重建；**只有 `bscore` 的核心算法与状态文件没有任何备份**，由此建立本仓库作为长期存档。

## 现状：已恢复 / 已丢失

| 项目 | 状态 | 说明 |
|---|---|---|
| `bscombo/rebuild_bscombo.py` | ✅ 已恢复 | 21.6 KB，从 2026-10-21 会话记录中逐字回收，语法校验通过 |
| `tools/*` | ✅ 已入库 | 看板增量刷新器、过期横幅、备份回收与凭证扫描工具 |
| `tools/template_v23.html` | ✅ 已入库 | 五章式看板完整模板，是所有展示口径的可执行参照 |
| `bscore/src/daily_update.py` | ❌ **已丢失** | CB 主计算脚本。本机、云端、资料库三处均无副本 |
| `bscore/data/cache_universe.csv` | ❌ 已丢失 | 净资产与流通股本缓存 |
| `bscore/data/cb_history.csv` | ❌ 已丢失 | CB 逐日序列，历史分位的分母 |
| `bscore/data/latest.json` | ❌ 已丢失 | 每日结果快照 |

## 重要限制：云端访问不了 GitHub

2026-10-22 实测，云端沙箱内 `git clone https://github.com/...` 在 133 秒后超时：

```
fatal: unable to access 'https://github.com/mathskit-pure/pool-core.git/':
Failed to connect to github.com port 443 after 133509 ms: Couldn't connect to server
```

因此**本仓库不能直接用于云端恢复**。目前唯一可用的中转通道是资料库（云端能读写资料库节点）。
若将来云端出口恢复，`git clone` 即可。

## 目录

```
bscombo/rebuild_bscombo.py   组合模块重建脚本（净值曲线 / 回测对比 / 持仓变动）
tools/patch_v23.py           看板增量刷新器：只替换读数，不动结构
tools/run.sh                 云端调用入口：取页面 → 打补丁 → 写回
tools/template_v23.html      五章式看板模板（刷新器的兜底基线）
tools/stale_banner.py        数据过期横幅
tools/dig_codes.py           从会话记录里回收曾经写过的源码
tools/read_diag.py           读取「云端体检」诊断页
tools/check_secrets.py       推公开仓库前的凭证扫描
```

## 重建 bscore 需要做什么

1. 按 [`SPEC.md`](SPEC.md) 重新实现 `daily_update.py`（口径已知，历史面板需重新抓取）。
2. 重建 `cache_universe.csv`：全市场净资产 + 流通股本。
3. 重建 `cb_history.csv`：这是最贵的一步，需要全市场历史 PB / BM 面板才能算出分位。
   没有它，CB 的历史分位没有分母，看板读数无法复现。
4. 状态恢复后立即打包上传资料库，并要求云端任务**每次运行结束都把状态回传**，避免再次归零。

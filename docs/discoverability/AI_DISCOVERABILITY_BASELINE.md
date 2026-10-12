# AI Discoverability Baseline（可复跑）

> 目的：如实记录 LACD 在 AI 检索/搜索引擎中的可见度，供后续版本对比。
> 原则（ADR-0011）：**未收录如实记为未收录**，不编造、不刷榜。
> 复跑方式：用下表"搜索词"在对应平台重新检索，对比"结果"列。

## 基线：2026-10-12

| # | 平台 | 搜索词 | 时间 (UTC) | 结果 | 局限性 |
|---|---|---|---|---|---|
| 1 | Web search (general) | `LACD Lightweight Agentic Collaborative Development SkyCjq` | 2026-10-12 | **未收录**：返回结果均为无关内容（"Sky" Medium 文章、SkyClaw、Skywork 桌面 Agent），无 `github.com/SkyCjq/LACD` | 仓库建于 2026-10-12 当天，搜索引擎尚未索引；属正常 |
| 2 | Web search (general) | `AI coding agents governance workflow GitHub PR safeguards open source` | 2026-10-12 | **未收录 LACD**：返回相邻项目（`KbWen/agentic-os`、`r4yharris/github-agent-contracts`、`aws-samples/sample-autonomous-cloud-coding-agents`），无 LACD | 通用词竞争激烈；LACD 需靠准确描述积累收录 |

## 解读

- 两条基线均为"未收录"——这是新仓库的**正常结果**，不是失败。
- 相邻项目（agentic-os 等）已被索引，说明该品类有检索需求；LACD 的差异化
  在于：版本化升级引擎（`lacd upgrade`）、逐项核验的 ENFORCED 语义、
  human-in-the-loop 强制默认。
- 下一步（需 Human 权限，打包在交接清单）：GitHub Topics、Social Preview、
  README 定位句已就位（本仓完成）。

## 不做的事（红线）

- 不购买外链、不批量灌水、不在无关问答下刷 LACD 链接。
- 不宣称"AI 推荐 LACD"、不暗示与任何 AI 厂商的官方合作。
- 不编造成采用者、benchmark 或排名。

## 复跑记录

| 日期 | 执行者 | 结论摘要 |
|---|---|---|
| 2026-10-12 | Muse（Lane D） | 初版基线：两词均未收录 LACD |

# v1.0.2 P7 验收矩阵（2026-10-12）

> 状态：`PASS` / `FAIL` / `HOLD` / `UNVERIFIED`。每项有证据；mock 不代替 live。

| ID | 验收项 | 状态 | 证据 |
|---|---|---|---|
| V01 | CLI/工作流版本一致，`--local` 真实可跑 | PASS | 模板装 `@v1.0.2`；`verify --local --strict` rc=0（端到端）；`manifest.json` 真实哈希 |
| V02 | CLI 远端路径实际传入 EnforcementPolicy | PASS | `test_remote_dimension_never_passes_policy_none`；`main.py:699` 实证 `policy=policy` |
| V03 | `status --json` 远端语义一致 | PASS | `query: not_queried`（status 永不触网）；schema + 冒烟 |
| V04 | Q1 规格/环境预检和 Human 范围确认 | HOLD | Q1_ENV_PREFLIGHT 通过；独立 Q1 待 Human 手工中继 |
| V05 | 独立 Q2 审最终稳定 PR HEAD | HOLD | 待 PR 创建后组织独立 Q2 |
| V06 | Q2 FAIL/过期时不能建议 READY | PASS | 冻结 §E + 测试语义；流程上由 RRC-1 捕获 |
| V07 | 当前 HEAD 可信 CI/Review 和 Human Merge 可追踪 | HOLD | 待 PR + Human 合并 |
| V08 | Q2 不要求旧机器评论格式 | PASS | ADR-0012；PROTOCOL.md §5 无旧格式 |
| V09 | 干净 wheel/模板安装成功 | PASS | `test_packaging.py` 13 测试零 skip；381 passed |
| V10 | 真实可丢弃仓接入/升级 PR + CI | HOLD | 需 Human 批准建测试仓（LIVE） |
| V11 | `main` 保护/Required Checks 确实强制 | HOLD | Human 管理员核实（API 403 不可读） |
| V12 | dry-run/冲突/回退/重复升级 | PASS | 全量测试；`test_upgrade_*` 无退化 |
| V13 | 合并 SHA 的 RRC 完成 | HOLD | 待 Human 合并后执行 `docs/operations/rrc.md` |
| V14 | v1.0.2 Tag/Release + PRV | HOLD | 待 Human 发版 |
| V15 | README/CHANGELOG/llms.txt 版本一致 | PASS | 指向 `v1.0.1`（已发布）；1.0.2 标 `(unreleased)` |
| V16 | LACD 无特定业务项目内容 | PASS | 全仓 grep 零命中 |
| V17 | 3.11/3.12 全套测试 | PASS | 本地 381 passed, 1 skipped；CI 待 PR 触发 |
| V18 | Human 保留合并/发版/权限决策 | HOLD | 待实际发生 |

**Q1 状态**：浏览器路线被 Cloudflare 阻断 → 已转 Human 手工中继（prompt 已交付）。
实现并行推进中；若 Q1 FAIL 则有界修复。

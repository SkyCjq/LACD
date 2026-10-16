# ADR-0014：默认上游仓库迁移（SkyCjq/LACD → SkyOneBranch/LACD）

- 状态：已接受（2026-10-16，v1.0.3 Freeze，HD-03）
- 背景：仓库已从 `SkyCjq/LACD` 迁移至 `SkyOneBranch/LACD`（GitHub 保留旧地址
  重定向）。v1.0.2 的全部默认上游仍指向旧仓库；长期不跟进则新采用者默认走
  重定向链路，且文档/模板与仓库现实脱节。

## 决定

1. 新默认上游为 `SkyOneBranch/LACD`：`src/lacd/upgrade/source.py`、
   `src/lacd/config/models.py`、`src/lacd/cli/main.py` 的
   `DEFAULT_UPSTREAM_REPO`，两份 `lacd-config.schema.json` 的 `default`，
   受管工作流模板的 `pip install` 地址，release/rrc/install/runbook 文档 URL。
2. 解析优先级不变：`$LACD_UPSTREAM_REPO` > `lacd.yaml` 的 `upstream_repo`
   显式配置 > 内置默认。显式配置永不被新默认值覆盖。
3. 旧采用者兼容：v1.0.2 `init` 已显式写入 `upstream_repo: SkyCjq/LACD` 的项目
   继续走旧来源（经重定向解析）；旧 `.lacd/lock.json` 的
   `source.repo=SkyCjq/LACD` 可读取、可解析；读取/检查/重定向过程中不得重签
   SHA-256；旧项目不因 v1.0.3 发布而自动 DRIFT。
4. 历史真实记录不动：ADR-0006/0013、BOOTSTRAP.md、v1.0.0/v1.0.1 release
   fixture、CHANGELOG 历史章节中的旧来源字符串保留。

## 替代方案

- 保持旧默认、依赖重定向：被否决——新采用者默认走重定向是不必要的间接层，
  且与"来源身份可诊断"原则冲突。
- 迁移时重写旧 lock 的 `source.repo`：被否决——违反"不擅自重签"原则；
  旧值经重定向仍可解析，无需改写。

## 风险与回退

- 旧仓库重定向若未来解除，旧来源解析失败按 fail-closed 报 UNVERIFIED/错误，
  不静默成功（测试 B-5）。
- 本 ADR 变更触及冻结合同 `docs/specification/UPGRADE_CONTRACT.md` §4 与
  `docs/architecture/MODULE_CONTRACT.md` §2.1/§2.2，已随 v1.0.3 Freeze 一并批准。

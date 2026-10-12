# v1.0.1 P7 验收矩阵（2026-10-12）

> 状态：`VERIFIED`（有证据）/ `TESTED`（测试过）/ `UNVERIFIED` / `BLOCKED` /
> `HOLD`（等 Human/外部）。不接受自述，每项有证据类型。

## R：功能/安全（MUST 除非标注）

| ID | 验收项 | 状态 | 证据 |
|---|---|---|---|
| R01 | clean wheel 安装后 CLI/Schema/templates 正常 | VERIFIED | 隔离 venv：`pip wheel`→安装→`lacd init`/`status --json`/`verify --local` 全过（13 测试零 skip） |
| R02 | 打包失败不能 silent skip | VERIFIED | `test_packaging.py` 13 测试；安装失败即 fail，无 skip 分支 |
| R03 | 真实 v1.0.0 Release 元数据可解析或明确报错 | VERIFIED | 真实 API body 做 fixture → 新 parser 返回 None（fail-closed）；闭合围栏/`Template-Bundle-SHA256:` 行分别解析成功 |
| R04 | v1.0.1 Release notes/bundle/manifest/asset digest 校验 | HOLD | 等 Human 发 Release；模板与校验清单已在 `docs/operations/release.md` |
| R05 | init 在全新 Alpha 项目幂等 | VERIFIED | `test_cli_init.py` + 全量 370 passed |
| R06 | Beta 保留用户 CI/AGENTS.md | VERIFIED | 逐字节 diff 测试 |
| R07 | 采用者低权限 PR CI 可跑本地验证 | VERIFIED | 模板用 `verify --local`；`--local` 零网络有 monkeypatch 证明；真实 Actions run 待 PR CI |
| R08 | 本地绿不报 ENFORCED | VERIFIED | 负例：`--local` 输出断言含 `unverified`+`LOCAL_ONLY`，无 `enforced` |
| R09 | ENFORCED 须含 Required Checks/PR 保护 | VERIFIED | `EnforcementPolicy` 19 策略测试；`details.items` 逐项 checked/satisfied |
| R10 | disabled/nonmatching ruleset、bypass、403/404 不假 ENFORCED | VERIFIED | 12 项负例矩阵（见 Lane D 报告） |
| R11 | status/verify 本地远端语义一致且有文档 | VERIFIED | `query: live\|not_queried` 字段；`status` 永不触网（socket monkeypatch） |
| R12 | 版本锁发现与 CLI 差异 | VERIFIED | 四版本一致性测试；mock `0.0.0` → mismatch |
| R13 | 有 SHA 则记录核验，无 SHA 不谎称 strong | VERIFIED | 畸形 SHA（39/41 位、非 hex）→ 永不 strong |
| R14 | `upgrade --check` 从真实 Releases 发现新包 | VERIFIED | 真实 v1.0.0 Release fixture；fake API 集成 |
| R15 | 源不可用不误报已最新 | VERIFIED | 错误测试（AT-12 延续） |
| R16 | dry-run 零写入 | VERIFIED | 全树 sha256 对比 |
| R17 | 无冲突迁移幂等 | VERIFIED | 重复执行零 diff；`require_bundle` 不影响 dry-run |
| R18 | 修改受管文件阻断且原文件不动 | VERIFIED | 负面测试 |
| R19 | 迁移失败回滚到可解释快照 | VERIFIED | 故障注入 |
| R20 | 升级 PR 只建 feature branch | VERIFIED | fake API + 本地 git；去重/不推 main |
| R21 | 真实可丢弃远端仓升级 PR 演示 | HOLD | 需 Human 批准建测试仓（LIVE/HUMAN） |
| R22 | 旧 HEAD 证据不能当新 HEAD 证据 | VERIFIED | SHA mismatch 测试 |
| R23 | 安全策略/CI trust boundary 负例；Actions pins 可核实 | VERIFIED | 模板 pins 为完整 SHA；`ci.yml` pinned 版已备好待 Human 网页建 |
| R24 | 规范/README 与真实一致；无特定业务名 | VERIFIED | `grep -rni` 全仓零命中；PRODUCT.md 已清 |
| R25 | `.coverage`、`build/` 从跟踪树清理 | VERIFIED | `git ls-files \| grep` = 0；CI 加断言 |
| R26 | main 原生 PR+Required Checks 真正强制 | HOLD | Human 在 GitHub UI 核实（LIVE/HUMAN） |
| R27 | release tag 指向批准 HEAD；digest 全匹配 | HOLD | 等 Human 发 Release 后只读终验 |

## D：可发现性

| ID | 验收项 | 状态 | 证据 |
|---|---|---|---|
| D01 | 英文定位与关键词准确不夸大 | VERIFIED | README 重写；每句 capability 有冒烟测试对应 |
| D02 | 中英关键功能一致 | VERIFIED | 双向链接；语义对照 |
| D03 | GitHub About/Topics/Homepage | HOLD | Human 管理员设置（清单已备） |
| D04 | Quickstart/FAQ/Limitations 可机读 | VERIFIED | docs smoke 测试 |
| D05 | 无死链、图像 alt、许可齐全 | VERIFIED | 手动链路检查；本版无新增图片 |
| D06 | llms.txt 不谎称标准 | VERIFIED | 头顶声明实验性非跨平台标准 |
| D07 | 可复测检索基线 | VERIFIED | `docs/discoverability/AI_DISCOVERABILITY_BASELINE.md`（两次真实查询，未收录如实记） |
| D08 | Pages（条件） | HOLD | 未启用；无权限不宣称 |
| D09 | 不刷榜不造假 | VERIFIED | 内容审查；红线写进 ADR-0011 |
| D10 | 社交预览图 | HOLD | Human 可选（1280x640 <1MB 规范已给） |

## C：采用者（POST-RELEASE 门槛）

C01–C07 全部 `HOLD`：v1.0.1 Release + R26/R27 终验通过后，才启动采用者只读审计与接入 PR。

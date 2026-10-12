# LACD v1.0.1 规格增量（Freeze，2026-10-12）

> 基于 v1.0.0（tag `v1.0.0` → `36df9d5c`）。v1.0.1 是**补丁版本**：public CLI
> 与 JSON Schema 保持向后兼容；所有新增为加法。若发现不可避免的 breaking
> change，必须先出 ADR 并交 Human 决策，不得默默包装成 patch。

## 兼容性承诺

- `lacd init/doctor/status/verify/upgrade` 的已有参数与退出码语义不变。
- `lacd.yaml` schema v1、`lock.json` v1、`status` JSON v1 不变；只增可选字段。
- v1.0.0 的锁文件、模板包、Release 可被 v1.0.1 正常读取和升级。

## D1. 打包与安装（FR-01）

- wheel 必须包含 `templates/`（全部受管模板 + `manifest.json`）与 `schemas/`。
- CLI 通过 `importlib.resources` 定位模板/Schema（wheel、editable、源码树三态可用），禁止硬编码 `Path(__file__)` 相对路径。
- `build/`、`.coverage`、`__pycache__` 不得被跟踪；`git ls-files` 与 `.gitignore` 对齐。
- 安装测试：`pip wheel` → 全新 venv 安装 wheel → 全新 git 仓跑 `init`/`status --json`/`verify`。
  安装失败**必须 fail**，不得 `skip` 当通过（live 权限类测试除外，显式标 UNVERIFIED）。

## D2. Release 模板包发现（FR-02）

`upgrade/source.py` 的 bundle 解析支持三种格式（按顺序尝试，全部 fail-closed）：
1. 闭合的 ` ```json ` 围栏块内 `{"template_bundle": {"url": ..., "sha256": ...}}`；
2. 明文行 `Template-Bundle-SHA256: <64 hex>`（URL 取同 Release 的 `lacd-template*.(zip|tar.gz)` 资产）；
3. 兜底：Release 仅有一个 `lacd-template*.(zip|tar.gz)` 资产且 Release 正文含其 sha256 时绑定。

任何"找到 URL 但无可信 sha256"的情况 → 拒绝使用（fail-closed），不得下载未校验内容。
v1.0.1 的 Release notes 模板必须同时含闭合 JSON 块与 `Template-Bundle-SHA256:` 行。

## D3. verify 本地/远端分层（FR-03）

- `lacd verify --local`：只做本地维度；`remote_enforcement` 直接记
  `unverified/LOCAL_ONLY`，不发起任何网络请求。采用者 PR CI 默认用此模式。
- `lacd verify --remote`：尝试远端核验；失败时按原因码降级（`REMOTE_NO_CREDENTIAL` /
  `REMOTE_PERMISSION_DENIED` / `REMOTE_NETWORK_ERROR`），永不谎称 ENFORCED。
- 不带 flag 的 `lacd verify` 保持 v1.0.0 行为（尝试远端，失败降级），向后兼容。
- `lacd status --json` 的输出新增 `dimensions.remote_enforcement.query` 字段：
  `live`（本次真实查询）/ `not_queried`（本地模式）。`status` 本身不触发网络（保持快），
  远端语义由 `verify` 负责。

## D4. ENFORCED 安全语义（FR-04）

引入版本化 `EnforcementPolicy`（`policy_version: "1"`）：
- 输入：目标分支、要求的 Required Checks 列表（名称）、是否要求 PR、是否允许 bypass。
- `check_remote_enforcement(repo, branch, *, policy, token)` 逐项核验：
  classic protection 与 rulesets 分别处理；ruleset 须 `enforcement: active` 且 ref 条件匹配目标分支。
- 任一关键维度不可核验（403/404/字段缺失）→ `UNVERIFIED` + 缺失清单；明确不满足 → `NOT_ENFORCED`。
- **仅当全部策略项被证实，才返回 ENFORCED**。"PR requirement 存在"只是必要条件，不是充分条件。
- Solo 仓 0 approvals：允许，但报告必须写明 `human_merge_required` 且不得伪装成独立 approving review。

## D5. 版本锁定与升级（FR-05）

- `lock.json` 新增可选 `source.commit`（完整 SHA）；有 SHA 时 `verify` 核验，无 SHA 时标记 `pin_strength: "tag-only"`（不得谎称 strong pinned）。
- 采用者 CI 模板优先使用**已核验完整 SHA** 引用第三方 Actions；`pip install` 行使用 tag（tag 不可变性由 Release 流程保证，文档明示）。
- 其余（check/dry-run/幂等/冲突/回退/prepare-pr）语义不变，回归测试覆盖。

## D6. Agent 协议（FR-06）

- `AGENTS.md = 入口提示`、`status --json = 机器状态` 原则不变。
- 新增 `docs/agents/`：厂商无关的可选快速接入文档（不宣称"所有 AI 自动识别"）。

## D7. 独立性与仓库整洁（FR-07）

- 全仓扫描：产品文件（源码/README/Docs/示例/测试/Schema/宣传资产）不得含特定业务项目名。`PRODUCT.md:43` 的提及删除。
- `git rm --cached` 移除 `.coverage`、`build/`；补 `.github/CODEOWNERS`、PR/Issue 模板。
- LACD 主仓 `.github/workflows/ci.yml` 的第三方 Actions 固定完整 SHA（Human 网页操作，Muse 交付精确文件）。

## D8. 可发现性内容准则（DISC）

- 允许说：真实功能、真实测试数据、真实限制、"open-source"、"human-in-the-loop"。
- 禁止说：未证实的 AI 推荐/排名保证、与官方合作/认证、"zero hallucinations"、"automatic secure merge"、编造的采用者/benchmark。
- 英文定位句（Human 终审）：
  "Open-source governance workflow for AI coding agents: verifiable adoption, GitHub PR safeguards, human review, and safe versioned upgrades."
- `llms.txt` 为实验性入口，必须声明非跨平台标准。

## Fail-closed 清单（本版新增）

| 场景 | 行为 |
|---|---|
| Release 无可信 bundle sha256 | 拒绝下载，`UpgradeRefused(bundle-unverified)` |
| 远端策略任一项不可核验 | `UNVERIFIED` + 缺失清单，不报 ENFORCED |
| wheel 缺模板 | 安装测试 fail（CI 红），不 skip |
| 低权限 CI 读不到保护 API | 本地验证继续，远端记 `LOCAL_ONLY`/`UNVERIFIED`，不永久红灯 |
| 文档 capability 断言 | 必须对应真实可运行功能（README 审计） |

## 绝对不能自动化的操作

- 修改/放宽 Branch Protection / Rulesets；新增/扩大 PAT/Secrets；PR 合并；tag/Release 发布；
  外部账号授权与站点所有权；生产系统不可逆变更。

# LACD — 轻量智协开发

## 1. 一句话介绍

> LACD（轻量智协开发）是一套无需本地开发环境的开源 AI 协作开发框架，支持 Muse 等 AI Agent 直接基于 GitHub 开发项目，通过自动化测试、GitHub 安全门禁、第三方 AI 独立审查与人工最终审批，实现高效、可验证、可追溯的软件开发。

**"无需本地开发环境"是指**：你可以选择不安装本地 IDE，走 GitHub 云端开发路径。**不是**指工具链消失——CI 运行器、Python、Git 和执行环境仍然需要（见 §5 安装前提）。

> **状态：v1.0.2 为最新已发布版本。**
> 安装：`pip install "git+https://github.com/SkyOneBranch/LACD.git@v1.0.2"`。
> v1.0.3 正在开发中，**尚不可安装**——不要使用 `@v1.0.3`。
> Tag、GitHub Release、分支保护仍由人工执行（见
> [docs/operations/human-approval.md](docs/operations/human-approval.md)）。

<img width="1774" height="887" alt="LACD Workflow Infographic Poster" src="https://github.com/user-attachments/assets/e2d6db18-e33a-4434-b3ec-6f4fc1f16c96" />

## 2. 30 秒理解 LACD

**LACD 能做什么**

- **可验证的接入**：`lacd init` 在 `lacd.yaml` 中声明意图；`lacd status --json` 输出机器可读的采用状态（`NOT_INSTALLED` / `DECLARED` / `VALIDATED` / `ENFORCED` / `DRIFTED` / `BLOCKED` / `UNVERIFIED`）；`lacd verify` 负责核验。Agent 以 CLI 状态为准，不靠猜。
- **GitHub 原生安全门禁**：只读核验分支保护与 rulesets（每个策略项独立核验，读不到就报 `UNVERIFIED`，不猜）；升级 PR 附带证据正文，**永不自动合并**。
- **人工最终决策**：每个 PR 都由人合并。0 required approvals 的 solo 仓库被允许，但会如实报告 `human_merge_required`——绝不伪装成独立审批。
- **安全的版本化升级**：`lacd upgrade` 发现版本、`--dry-run` 预览全部变更、三方合并迁移、冲突时拒绝（不静默覆盖）、保留备份、可回退。模板包使用前必验 SHA-256。
- **风险分级变更**：每次文件变更定为 **T0**（文档）/ **T1**（代码/测试）/ **T2**（工作流/秘密/治理）；未知路径按 T2 处理（fail-closed）。

**LACD 不做什么**

- 不替你写代码、不评价代码质量、不判定对错——它是治理脚手架，**不是 AI 模型**，也**不内置任何 AI 编码或审查服务**。Agent（Muse 或其他）由你自己带来，LACD 只管它的工作如何被验证。
- 不合并 PR、不改分支保护、不管秘密、不发版——这些永远归人。
- 不回传数据：无中央服务器、无 SaaS、无遥测。
- **不承诺**任何 AI 搜索或推荐：某个 AI 助手是否推荐 LACD 取决于对方系统的检索与模型行为，我们控制不了（见 [ADR-0011](docs/decisions/ADR-0011-discoverability.md)）。

## 3. 使用 AI Agent 进行 GitHub 开发的角色分工

**AI 编码不是最终验收。自动测试、独立审查和 Human 决策共同构成交付治理。**任何检查都不能发现所有问题——治理降低风险，不消除风险。

| 角色 | 能力 | 责任 | 边界 |
|---|---|---|---|
| **Human**——产品经理和最终验收负责人 | 写需求与验收标准；批准、合并、发布 | 对范围、风险、合并、发布的最终决定；承担后果 | 人工决策就是决策——**不等于**每个仓库都必有 GitHub `APPROVED` 审查记录 |
| **Muse 或其他 AI Agent**——开发执行者 | 在分支上写代码、跑测试、准备 PR | 按需求实现；提供绑定 commit SHA 的证据 | 永不合并；永不批准自己的工作；永不改分支保护 |
| **GitHub Actions**——自动测试和检查系统 | 每次推送/PR 自动跑测试与检查 | 客观、可重复的证据（CI 日志） | 只能判定测试断言覆盖的东西；CI 通过不等于门禁已强制 |
| **Independent AI Reviewer**——与开发者隔离的独立质量审查者 | 在与开发隔离的会话中审查 PR 最终 HEAD | 独立于实现者上下文的判断，附独立证据 | **同一 Agent 在同一会话里自查，永远不算独立审查**；"第三方"指*与开发过程隔离*，不是指获得某企业认证或商业审计 |
| **GitHub + Human**——技术门禁与最终交付决定 | 分支保护、必需检查、人工合并 | 代码落地前的门禁执行 | 门禁只在真实配置后才生效——要核验，不要想当然；见 §8 |

## 4. 适用用户与非适用情景

**适合谁**

- **没有本地 IDE 的个人开发者**：想直接用 AI Agent 在 GitHub 上开发，又想要可审计的护栏，而不是只靠提示词。
- 在 GitHub 上用 AI 编程 Agent 的团队：想要可验证的采用状态和受控升级。
- 想在多个项目间安全地共享工作流模板的独立维护者。
- 希望 "human-in-the-loop" 是强制默认而不是文档建议的人。

**什么时候别用 LACD**

- 想要托管面板或 SaaS——LACD 按设计没有。
- 想要自动合并——LACD 按设计拒绝。
- 不用 GitHub：远端强制核验与 PR 准备是 GitHub 专用的（核心 CLI 的 init/status/verify/upgrade 可用于任何 git 仓库）。

## 5. 安装前提

- **Python 3.11+**（CI 测试 3.11 与 3.12）。运行时依赖仅 `pyyaml`、`jsonschema`。
- **Git** 与 **GitHub 账号**（走 GitHub 云端路径、做远端强制核验都需要）。
- LACD v1.0 **未**发布到 PyPI。请从可验证的源安装：

```bash
# 最新已发布版本（2026-10-16 已实测可安装）：
pip install "git+https://github.com/SkyOneBranch/LACD.git@v1.0.2"

# 或固定到精确 commit（可复现性最强）：
pip install "git+https://github.com/SkyOneBranch/LACD.git@<40位commit>"

# 本地开发：
git clone https://github.com/SkyOneBranch/LACD.git && cd LACD && pip install .
```

验证安装：

```bash
lacd --version   # 上面方式安装应输出 1.0.2
```

> 下一个开发目标是 **v1.0.3**（仓库迁移兼容、版本/模板可靠性、本 README 重写）。
> 它**尚未发布**——`@v1.0.3` 装不了。

同一机器上多个项目锁定**不同** LACD 版本时，请按版本用独立 venv
（见 [docs/operations/install.md](docs/operations/install.md)）。

## 6. 新手快速开始

```bash
cd your-project
lacd init --dry-run   # 预览拟写入内容，不写盘
lacd init             # 生成 lacd.yaml、.lacd/lock.json、受管模板、AGENTS.md 章节
lacd status           # 采用状态
lacd verify --local --strict   # 本地检查必须通过，否则非零退出
```

`init` 幂等（跑两次会提示 `already-initialized`），不执行 `git add`/`commit`/`push`，
不静默覆盖你的文件。详见 [docs/operations/adopt.md](docs/operations/adopt.md)。

真实运行记录（2026-10-16 对开发构建 v1.0.3 实测）。已发布的 v1.0.2 接受相同的命令、流程一致；其输出中的版本号为 `1.0.2`、默认上游为之前的 `SkyCjq/LACD`，与下方示例不同：

```text
$ lacd init --dry-run
DRY-RUN init for /tmp/lacd-readme-demo: no files will be written
  create         lacd.yaml  (default lacd.yaml (schema_version 1))
  create         .lacd/lock.json  (source git-tag SkyOneBranch/LACD@v1.0.3)
  create         .lacd/project-rules.yaml  (empty project-rules template)
  create         .github/workflows/lacd-verify.yml  (managed CI workflow template)
  create         AGENTS.md  (new file with managed section)

$ lacd init
initialized LACD 1.0.3 in /tmp/lacd-readme-demo
  wrote lacd.yaml, .lacd/lock.json, .github/workflows/lacd-verify.yml
  AGENTS.md managed section: created
Note: lacd never runs git add/commit/push; review and commit yourself.

$ lacd status
LACD status for /tmp/lacd-readme-demo
  state: VALIDATED
  cli: 1.0.3 | branch: unknown | head: unknown | mode: lacd
  local_validation: pass
  remote_enforcement: unverified (REMOTE_NOT_CHECKED)
  drift: clean
  blocking: none
  version_consistency: consistent - running CLI 1.0.3 matches locked lacd_cli_version (cli=1.0.3 package=1.0.3 locked_cli=1.0.3 template=1.0.3)

$ lacd verify --local --strict
LACD verify for /tmp/lacd-readme-demo
  mode: local-only (remote enforcement not queried)
  state: VALIDATED
  local_validation: pass
  remote_enforcement: unverified (LOCAL_ONLY)  [remote check skipped (--local): no network requests were made; remote enforcement is NOT verified, only local dimensions]
  drift: clean
  blocking: none
  version_consistency: consistent - running CLI 1.0.3 matches locked lacd_cli_version (cli=1.0.3 package=1.0.3 locked_cli=1.0.3 template=1.0.3)
STRICT: all local dimensions satisfied (remote not queried -- --local mode)
```

## 7. 从需求到测试、独立审查和 Human 合并的真实示例

LACD 面向用户的交付流程（内部 Q1/Q2 代号是 LACD 自家流程术语，**不是**行业通用标准）：

`Requirement → Acceptance Criteria → AI Agent Development → CI & Testing → Independent AI Review → Human Decision → Merge`

1. **Requirement → Acceptance Criteria（需求→验收标准）。** 人先写清楚"做什么"和"怎样算做完"。需求模糊是 Agent 白干活最常见的原因——先写验收标准。
2. **AI Agent Development（AI Agent 开发）。** Agent（Muse 或其他）在分支上实现：读 `AGENTS.md`、跑 `lacd status --json`、给改动定级（T0/T1/T2）、实现、提交前跑 `lacd verify`。证据绑定 commit SHA。
3. **CI & Testing（CI 与测试）。** Agent 推送分支，GitHub Actions 跑测试。CI 通过是客观证据——但不等于分支保护已强制（见 §8）。
4. **Independent AI Review（独立 AI 审查）。** 独立审查者——与开发隔离的另一个 AI 会话、附独立证据——审查 PR 的最终 HEAD。**同一 Agent 在同一会话里自查是自查，永远不是独立审查。**
5. **Human Decision（人工决策）。** 人看需求符合度、CI 证据、独立审查结论，然后批准并合并。LACD 只准备 PR，**永不**自动合并。
6. **Merge（合并）。** 合并就是交付决定。证据按合并 SHA 归档。

复杂项目或多 Agent 并行开发时，LACD 自家团队还会用开发前规格评审和冻结需求——这是推荐的前置治理实践，不是 CLI 功能，更不宣称 v1.0.2 CLI 已提供 Q1 签发或 Freeze 强制门禁。

## 8. LACD 的安全能力与限制

**安全能力**

- **Fail-closed**：远端状态不可验证时报 `UNVERIFIED`，永不报 `ENFORCED`。每个强制策略项独立核验，任一项读不到就给 `UNVERIFIED` + 缺失清单。
- **不静默覆盖**：迁移冲突时中止而非合并；项目自有文件永不被升级触碰。
- **下载必验**：模板包使用前验 SHA-256；下载内容永不作为脚本执行。
- **最小权限**：token 只从环境变量读，永不出现在日志、URL 或报告中；CI 模板的第三方 Actions 固定到完整 commit SHA。
- **证据绑定**：测试/评审证据记录 `head_sha`；`lacd verify --evidence` 拒绝过期绑定。
- **策略信任边界**：远端强制策略从受保护基线（`origin/main`）读取，不读未合并的 PR 文件——PR 不能给自己降门槛。

**限制（依赖 LACD 之前请先读）**

- 远端强制核验需要有读分支保护/rulesets 权限的 GitHub token；没有时结果为 `UNVERIFIED`（如实报告，不是错误）。**CI 通过不等于 GitHub 已强制。**
- 必需检查和审批规则是否真的强制生效，取决于你仓库的真实配置——去仓库设置里核验，LACD 只报告它能证明的。
- 尚无签名基础设施：发布完整性依赖 tag 不可变性 + Release notes 公示的 bundle SHA-256。
- 未发布到 PyPI；从 git tag 或 commit 安装。
- 每个环境一个 CLI 版本；多版本共存用 venv。
- `llms.txt` 是实验性的，不是跨平台标准。

## 9. 初始化、版本锁和升级的使用方法

**初始化**——见 §6。版本锁（`.lacd/lock.json`）记录精确 CLI 版本、可信来源（新项目为 `SkyOneBranch/LACD`）、模板版本和每个受管文件的 SHA-256。

**查状态**

```bash
lacd status --json   # 机器可读：主状态 + 四个维度 + 版本信息
lacd verify          # 本地检查 + 可证时的远端强制核验
```

**安全升级**

```bash
lacd upgrade --check                    # 发现版本：区分 Patch/Minor/Major
lacd upgrade --dry-run --to 1.0.3       # 预览全部变更；不碰工作树
lacd upgrade --to 1.0.3                 # 迁移：备份 → 三方合并 → 更新锁
lacd upgrade --to 1.0.3 --prepare-pr    # 生成分支 + PR（永不自动合并）
```

升级规则：冲突时中止，不覆盖你的改动；备份保留在 `.lacd/backup/` 下，可回退；重复执行同一版本幂等。版本锁、兼容性策略、无中央登记库原则见
[docs/specification/UPGRADE_CONTRACT.md](docs/specification/UPGRADE_CONTRACT.md)。

## 10. 常见问题

**LACD 支持我的 AI 编程助手吗？**
LACD 厂商无关，且不内置 AI。只要能跑 shell 命令的助手都能按 [docs/agents/](docs/agents/) 接入。我们不宣称任何产品会自动识别或背书 LACD；这里的"多 Agent"指角色可分离、工具可替换，不保证任意 AI 平台能力相同。

**AI 搜索引擎会推荐 LACD 吗？**
我们保证不了——这取决于各系统的检索与模型行为。见 [ADR-0011](docs/decisions/ADR-0011-discoverability.md) 与我们的[检索基线](docs/discoverability/AI_DISCOVERABILITY_BASELINE.md)（未收录如实记为未收录）。

**PR 谁来合并？**
永远是人。LACD 只准备 PR，不合并。

**独立审查是 LACD 自己做的吗？**
不是。LACD 不提供内置审查服务。"独立"指审查者与开发过程隔离、附独立证据——是另一个独立的审查者和会话，绝不是同一 Agent 在同一会话里自己查自己。

**非 GitHub forge 能用吗？**
核心 CLI（init/status/verify/upgrade）可用于任何 git 仓库。远端强制核验与 PR 准备是 GitHub 专用的。

**有托管版吗？**
没有。LACD 按设计是本地优先：无服务器、无账号、无遥测。

**LACD 能保证我的代码正确或安全吗？**
不能。自动测试、独立审查和人工决策共同构成交付治理——降低风险，但发现不了所有问题，LACD 对此不做任何保证。

## 11. 进阶技术与治理文档

- [docs/specification/PRODUCT.md](docs/specification/PRODUCT.md) — 目标、非目标、独立性铁律、术语
- [docs/specification/PROTOCOL.md](docs/specification/PROTOCOL.md) — 模式、Agent、角色、状态机
- [docs/specification/UPGRADE_CONTRACT.md](docs/specification/UPGRADE_CONTRACT.md) — 版本、升级、回退
- [docs/architecture/MODULE_CONTRACT.md](docs/architecture/MODULE_CONTRACT.md) — 冻结的模块接口
- [docs/agents/](docs/agents/) — 可选的 Agent 接入文档
- [docs/decisions/](docs/decisions/) — 架构决策记录
- [docs/operations/](docs/operations/) — 运行手册（安装 / 接入 / 升级 / 排障 / 人工审批 / 发布）
- [SECURITY.md](SECURITY.md)、[CONTRIBUTING.md](CONTRIBUTING.md)、[CHANGELOG.md](CHANGELOG.md)

**仓库结构**

```text
src/lacd/            # CLI 包
  cli/               # init/doctor/status/verify/upgrade 命令
  core/              # 状态机、status JSON、semver
  config/            # lacd.yaml Schema + .lacd/lock.json
  governance/        # T0/T1/T2 风险分级
  agents/            # Agent 协议检查单
  github/            # 远端强制核验、升级 PR 准备
  upgrade/           # 版本源、兼容性、三方迁移、回退
  templates/         # 受管模板（hash 记录在 manifest.json）
schemas/             # lacd-config / lock / status JSON Schema（版本化）
tests/               # unit / integration / acceptance（AT-01..AT-25）/ security
examples/generic-project/
docs/
  specification/     # PRODUCT / PROTOCOL / UPGRADE_CONTRACT（已冻结）
  architecture/      # 模块接口契约
  agents/            # 厂商无关的 Agent 接入文档（可选）
  discoverability/   # 检索基线（如实、可复跑）
  decisions/         # ADR
  operations/        # 安装 / 接入 / 升级 / 排障 / 人工审批 / 发布
llms.txt             # 实验性 Agent 入口（非标准）
```

**独立性保证**

LACD 永不包含业务项目名称、数据模型、凭证或工作流。通用能力已在两个
相互独立的合成仓库中验证（见 `tests/acceptance/`）。采用者只保留最小受管
入口、锁文件和可审计的项目扩展——升级就是决定何时合并一个受控 PR，
永远不需要复制代码。

[README.md](README.md) — English version.

## 许可证

MIT — 见 [LICENSE](LICENSE)。

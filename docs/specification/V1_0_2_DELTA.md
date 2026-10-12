# LACD v1.0.2 规格增量 v2（Re-Freeze，2026-10-12）

> 基于 v1.0.1（tag `v1.0.1` → `56dcfd2e`）。SemVer Patch。
> **v2 变更**：回应 Q1 FAIL（29 项），统一术语、补作弊定义、解时序依赖、
> 明确残余风险。v1 废止。

## E. 审查术语（统一版）

- **Q1** = 独立规格审查（verdict）+ `Q1_ENV_PREFLIGHT`（环境预检），二者都通过
  才算 Q1 通过；Human 范围确认是独立的前置步骤，不计入 Q1 verdict。
- **Q1_ENV_PREFLIGHT** 定义：Python 3.11/3.12 可用、依赖可安装、至少一组正例
  和一组负例测试可运行、GitHub API 可达且权限符合预期。由 Muse 执行并记录。
- **Q2** = 独立 Web AI 对 PR **最终稳定 HEAD** 的审查，Human 合并**前**。
  凭据字段（统一）：`reviewer`、`model`、`time`（UTC ISO8601）、
  `reviewed_head_sha`（完整 40 位）、`verdict`（`PASS`/`FAIL`/`HOLD`）、
  `raw`（原始结论全文）。保存位置：PR 描述或关联的审查记录文件，永久保留。
- **Q2 过期规则（统一）**：`reviewed_head_sha` ≠ PR 当前 HEAD → Q2 过期，
  不得推荐 `READY_FOR_HUMAN_REVIEW`。**无"实质变化"例外**——任何 HEAD 变化
  都触发重审（可判定性优先于便利性）。
- **RRC** = Human 合并后、Tag 前的制品/治理资格核验。**不是 Q2。**
- **PRV** = Release 后的 tag/资产/升级发现验证。
- **Human 手工中继** = 仅传递审查材料（prompt 去、原始回答回），
  **不是**独立审查的替代品；审查主体仍是独立 Web AI。
- Q2 FAIL/HOLD → 不得推荐合并。

### 状态判定边界（操作化）

| 状态 | 定义 | 可判定标准 |
|---|---|---|
| `ENFORCED` | 全部策略项被**证实**满足 | 每项有 `checked:true` + 证据来源；缺一不可 |
| `UNVERIFIED` | 任一关键项**不可核验** | 403/404/字段缺失/无凭据；附缺失清单 |
| `NOT_ENFORCED` | 已证实**不满足**策略 | 如：保护明确关闭、checks 为空且策略要求非空 |

"证据不足"（UNVERIFIED）与"已证实不满足"（NOT_ENFORCED）是不同状态，
不得混用；验收时分别断言。

## D1. 采用者模板版本修正（FR-102-01，P0）

- `lacd-verify.yml` 的 `pip install` 行必须与模板 `template_version` 一致
  （`@vX.Y.Z` tag 形式）。
- **版本边界**：模板是版本锁定的组件，**不是**"默认安装示例"。
  D5 的默认安装示例指向最新**已发布**版本（当前 `v1.0.1`）；D1 的模板在
  候选期指向待发布的 `v1.0.2`。候选期 = PR 创建后、Release 前。
- **时序依赖解**（回应 Q1 R3）：D1 的"真实 GitHub PR CI"在 **PRV 阶段**
  执行（tag 已存在后），不作为 RRC 前置门。RRC 只验证模板内容的静态正确性
  （版本号一致、哈希真实）。
- **验收绑定**（回应 Q1 A5）：真实 CI 必须记录运行 URL、运行 SHA、模板内容
  哈希、实际安装的版本、`verify --local --strict` 退出码。无关 PR 或旧版本的
  运行不得计入。

### 作弊定义（D1）
以下均不得算作"模板真实 CI 通过"：
- 安装了与模板声明不一致的版本
- 使用预装 LACD 的环境跳过 `pip install` 步骤
- 跳过 `verify --local --strict` 或允许其失败
- 引用其他 PR、无关分支或旧版本的成功运行记录

## D2. CLI 远端路径强制 EnforcementPolicy（FR-102-02，P0）

### 已验证配置（操作化，回应 Q1 R4/R5/R6）

**来源优先级**（高→低）：`lacd.yaml` 的显式配置 > `.lacd/lock.json` >
内置保守默认（空 `required_checks`）。

**信任边界**：配置必须来自仓库已提交的文件；PR 中新增的未合并配置
不得作为 policy 来源（防 PR 自降门槛）。

**required_checks 处理**：
- 空列表 → 该评估项缺席；若远端返回 ENFORCED → 降级为 UNVERIFIED
 （`REQUIRED_CHECKS_UNCONFIGURED`，fail-closed）
- 缺失字段 → 同空列表处理
- 重复项 → 去重后使用，记录警告
- 无法解析 → UNVERIFIED + `POLICY_CONFIG_UNPARSEABLE`

**凭据语义**（回应 Q1 R6）：
- `token=None` 且无 `GITHUB_TOKEN` → `UNVERIFIED` + `REMOTE_NO_CREDENTIAL`
 （不是错误，是明确的未验证状态）
- 403 → `UNVERIFIED` + `REMOTE_PERMISSION_DENIED` + 缺失清单
- 404 → `NOT_ENFORCED`（明确无保护）或 `UNVERIFIED`（子资源 404，见 ADR-0010）

**repo/branch 绑定**：`repo` 来自 git origin 解析；`branch` 来自当前分支或
显式参数；policy 的 `required_checks` 必须与目标分支的保护规则比对，
查询错误分支的结果无效。

### policy 内容验证（回应 Q1 A1）

测试必须验证 policy 的**内容**，不仅是非空：
- `policy.policy_version == "1"`
- `policy.required_checks` 与配置来源一致
- 入口点枚举：`_remote_dimension()` → `check_remote_enforcement()`；
  异常路径（无 origin、无模块）不得绕过 policy 构造

### 作弊定义（D2）
以下均不得算作"enforcement 验证通过"：
- 传入非空但内容错误的 policy（如空 checks 配"全部满足"）
- 清空 `required_checks` 后报 ENFORCED
- 查询错误分支或错误仓库的结果
- 隐藏 403/404/API 错误，报 UNVERIFIED 为"通过"
- 用 mock 的 ENFORCED 代替真实 API 结果（单元测试除外，live 验收必须真实）

## D3. Q1/Q2 SOP 文档化（FR-102-03，P0)

- `PROTOCOL.md` §5：四术语定义（本 §E）、Q2 凭据字段、过期规则、
  Human Merge 不可被 Q2 取代。
- **合并前复核责任人**（回应 Q1 S4）：Muse 在推荐 `READY_FOR_HUMAN_REVIEW`
  前必须核对 Q2 的 `reviewed_head_sha` == PR 当前 HEAD；Human 在合并前
  做最终确认。二者缺一不可。

### 作弊定义（Q2）
- 复用实现者的自审结论冒充独立 Q2
- 修改 Q2 原始结论的 `verdict`
- 冒用 `reviewer`/`model` 身份
- 将旧 `reviewed_head_sha` 的结论绑定到新 HEAD
- 上下文隔离最低要求：审查会话不得包含实现对话的任何内容

## D4. RRC 流程（FR-102-04，P0）

### 关键证据枚举（回应 Q1 A3）

| # | 证据 | 合格标准 |
|---|---|---|
| RRC-1 | 合并 SHA | `git rev-parse main` == PR merge SHA；与 Q2 的 HEAD 比对 |
| RRC-2 | CI | 该 SHA 的 3.11/3.12 runs 均为 success；job 名匹配 |
| RRC-3 | Required Checks | 管理员确认或只读 API 证实生效；不可读 → HOLD |
| RRC-4 | Q2 记录 | 按合并方式绑定：普通 merge 验证第二父提交 == Q2 HEAD；squash/rebase 用 PR 元数据+等价性证明，无路径时 HOLD。v1.0.2 release 要求普通 merge |
| RRC-5 | 制品摘要 | wheel/zip/manifest 的 SHA256 互相对应；与构建日志一致 |
| RRC-6 | Release notes | 可被 `parse_template_bundle` 解析（测试验证） |

**证据真实性**：每项必须有来源（URL/命令/截图）；**完整性**：六项缺一不可；
**有效性**：SHA 必须 40/64 位十六进制且与声称的构件匹配。

### Tag 核验时序（回应 Q1 R2/T3）

- **RRC（Tag 前）**：核验**预期**——合并 SHA 即将打的 tag 名称、Release notes
  草稿可解析、制品已就绪。**不核验 tag 本身**（尚不存在）。
- **PRV（Tag 后）**：核验**实际**——`git ls-remote` 的 tag → SHA 是否等于
  RRC 记录的合并 SHA；tag 被移动 → FAIL。

### 作弊定义（RRC/PRV）
- 用其他 SHA 的 CI/制品/摘要替代目标证据
- 用未实际发布的制品做验证
- 把缺失证据记为"不适用"而不经 Human 批准
- RRC 证据回写 main（改变被核验的 SHA）

## D5. 文档与可发现性修正（FR-102-07，P1）

- README（EN/ZH）、`llms.txt`、`CHANGELOG`：候选期标 `(unreleased)`；
  发布后按 Human 批准更新。
- **D5 更新触发重验**（回应 Q1 A6）：若发布后文档更新产生新提交，
  且该提交进入 main，则下一次 RRC/PRV 必须覆盖。**纯文档提交同样触发
  Q2 过期**（任何 HEAD 变化 → 重审，无例外）；但重审可为快速确认
  （scope 限于文档），PRV 的"文档一致性"项需更新。

### PRV 定义补全（回应 Q1 S4/A4）

**测试项目**：
- PRV-1：tag → SHA 等于 RRC 记录
- PRV-2：Release 非 draft，资产 SHA256 匹配
- PRV-3：`parse_template_bundle` 对真实 Release 正文解析成功
- PRV-4：干净环境 `pip install @vX.Y.Z` 成功
- PRV-5：`upgrade --check` 发现新版本

**通过阈值**：五项全 PASS。任一 FAIL → 禁止生产采用。
**未执行/证据缺失/HOLD**：一律视为未通过，不得采用。
**批准人**：Human（PRV 报告经 Human 确认）。
**阻断记录**：PRV 报告永久保存，FAIL 项列明原因和修复 PR。

## Fail-closed 状态表（完整）

| 场景 | 结果 | 可观察标准 |
|---|---|---|
| CLI 远端路径未传 policy | 测试失败 | mock 断言 `policy` 非 None 且内容正确 |
| Q2 SHA ≠ PR HEAD | Q2 过期 | `reviewed_head_sha` 比对；不得 READY |
| RRC 缺关键证据 | HOLD | 六项清单缺一不可 |
| PRV 任一 FAIL | 禁止采用 | 五项全 PASS 才放行 |
| PRV 未执行 | 禁止采用 | 无报告 = 未通过 |

## 残余风险声明（回应 Q1 R1）

- **Tag 可变性**：RRC/PRV 只能证明"核验时 tag 指向正确 SHA"，不能保证
  tag 永久不可变。账号失陷、强制推送、删除重建都可能改变 tag。
  这是**残余信任风险**，缓解措施：PRV 后定期抽查、关键采用锁定 SHA 而非 tag。
  不得表述为"已完全覆盖"。

## 绝对不能自动化的操作

- 修改/放宽保护；自批/自合；借用 Human PAT 冒签；Tag/Release 发布；
  生产系统不可逆变更。

### 作弊 vs 合法变更的边界（回应 Q1 C6）

**可观察标准**：删除测试/降低门禁是否附带 ADR + Human 书面批准 +
 Freeze 更新。若三者齐全 → 合法变更；缺任一 → 视为作弊（FAIL）。
动机不作为判定依据，只看可观察的批准链。

# LACD v1.0.2 规格增量（Freeze，2026-10-12）

> 基于 v1.0.1（tag `v1.0.1` → `56dcfd2e`）。SemVer Patch：public CLI 与
> v1 Schema（config/lock/status）保持向后兼容；新增为加法。
> 本次冻结同时做 **Q1/Q2/RRC/PRV 术语与 SOP 对齐**（见 §E）。

## E. 审查术语（与 Notion S0/S4 对齐，不另起炉灶）

- **Q1**：独立规格/验收审查 + `Q1_ENV_PREFLIGHT`，开发前；主 Agent 统合，Human 确认范围。
- **Q2**：独立 Web AI 对 PR **最终稳定 HEAD** 的审查，Human 合并**前**；
  只有 Q2 PASS + Required Checks 成功，才建议 READY_FOR_HUMAN_REVIEW。
  Q2 凭据：reviewer/模型、时间、`reviewed_head_sha`、原始结论；`PASS/FAIL/HOLD`。
  不恢复 `mode2-review` JSON 评论机器门禁、不强制 Freeze HTML。
- **RRC**：Human 合并后、Tag 前，对合并 SHA、CI、制品、发布元数据的核验。**不是 Q2。**
- **PRV**：Release 后对 tag/资产/升级发现的验证。通过后才批准生产采用。
- Q2 FAIL/HOLD → 不得推荐合并；HEAD 实质变化 → Q2 过期重审。

## D1. 采用者模板版本修正（FR-102-01，P0）

- `lacd-verify.yml` 不得安装与所调 CLI 功能不兼容的版本。
- 方案：模板 `pip install` 行改为安装与模板同版本的 LACD
  （`@v1.0.2` tag；tag 不可变性由 Release 流程保证，文档明示）。
  不得预先编造尚未产生的合并 SHA；不得用 `pipx` 浮动最新版。
- 验收硬门槛：合成真实 GitHub PR CI 运行（非静态 YAML 测试）。

## D2. CLI 远端路径强制 EnforcementPolicy（FR-102-02，P0）

- `_remote_dimension()` 必须基于当前项目已验证配置构造
  `EnforcementPolicy(policy_version="1", ...)` 并传入 `check_remote_enforcement`。
- `policy=None` 的 legacy 分支保留但标记 deprecated；**正常 CLI 主路径中禁止**——
  由测试断言：`_remote_dimension` 永不以 `policy=None` 调用。
- 缺 required checks / origin / token / 保护可读权限 / ruleset 适用证明 /
  bypass 信息 → 不得 ENFORCED（UNVERIFIED 或 NOT_ENFORCED）。
- `verify --local --strict` 不受远端 API 权限影响，但不声称 ENFORCED。

## D3. Q1/Q2 SOP 文档化（FR-102-03，P0）

- `PROTOCOL.md` 增量章节：Q1/Q2/RRC/PRV 定义、Q2 凭据要求、过期规则、
  Human Merge 不可被 Q2 取代。
- `AGENTS.md` / `CONTRIBUTING.md` 映射到同一术语；不得自创第二套名称。

## D4. RRC 流程（FR-102-04，P0）

- RRC 检查清单（脚本 + 运行手册）：合并 SHA、该 SHA 的 CI、Required Checks
  生效证据、Q2 记录、wheel/zip/manifest 摘要、Release notes 可解析性。
- RRC 证据不回写 main（防 SHA 循环）；结果 `PASS/FAIL/HOLD`。
- 仅 RRC PASS + Human Release Authorization → 打 Tag/Release。

## D5. 文档与可发现性修正（FR-102-07，P1）

- README（EN/ZH）、`llms.txt`、`CHANGELOG`：v1.0.2 发布前标候选，发布后按
  Human 批准更新为正式；默认安装示例指向最新已发布版本（当前 `v1.0.1`，
  v1.0.2 发布后更新）。
- 宣传语红线（ADR-0011 延续）：不承诺 AI 推荐/排名。

## Fail-closed 清单（本版）

| 场景 | 行为 |
|---|---|
| CLI 远端路径未传 policy | 测试失败（禁止 legacy 流经主路径） |
| Q2 `reviewed_head_sha` ≠ PR 最新 HEAD | Q2 过期，不得 READY_FOR_HUMAN_REVIEW |
| RRC 缺关键证据 | HOLD，不得打 Tag |
| PRV 失败 | 禁止生产采用 |

## 绝对不能自动化的操作

- 修改/放宽保护；自批/自合；借用 Human PAT 冒签；Tag/Release 发布；
  生产系统不可逆变更；为 PASS 而删测试/降门禁。

# v1.0.1 P0 基线证据（2026-10-12，不提交业务名到 LACD）

> 每个事实：时间、来源、SHA/结果、结论等级。失败已分类。

## 仓库状态（只读核实）

| 事实 | 证据 | 时间 (UTC) | 等级 |
|---|---|---|---|
| LACD `main` HEAD | `36df9d5c`（PR #1 merge） | 10-12 08:0x | VERIFIED |
| LACD tag `v1.0.0` → `36df9d5c` | `GET /git/refs/tags/v1.0.0` | 10-12 | VERIFIED |
| LACD Release v1.0.0 已发布 | `draft:false, prerelease:false`，附件 `lacd-templates-1.0.0.zip` 2659B，sha256 与本地构建一致 | 10-12 | VERIFIED |
| LACD description | `Lightweight Agentic Collaborative Development｜轻量智协开发` | 10-12 | VERIFIED |
| LACD topics | `[]`（空） | 10-12 | VERIFIED |
| LACD homepage | `null` | 10-12 | VERIFIED |
| LACD `main` protected | `protected:true`（classic；明细 API 403 不可读） | 10-12 | VERIFIED（存在性）/ UNVERIFIED（明细） |
| 采用者仓 `main` HEAD | `4219961c2640`，`protected:true` | 10-12 | VERIFIED（只读） |

## v1.0.0 缺陷复核（全部复现）

| # | 缺陷 | 复现 | 分类 |
|---|---|---|---|
| 1 | Release notes ` ```json ` 缺结束围栏 | 真实 API body 调 `_parse_template_bundle` → `None` | 代码缺陷（parser 无容错）+ 文档错误 |
| 2 | wheel 缺 templates | `pip wheel` → 33 文件、0 模板；干净 venv 安装后 `lacd init` → `FileNotFoundError` | 代码缺陷（P0 阻断） |
| 3 | 跟踪了 `.coverage`、`build/lib/*` | `git ls-files` 列出 | 仓库整洁 |
| 4 | 缺 `.github/CODEOWNERS` | `find` 无 | 流程缺失 |
| 5 | `ci.yml` 用 `@v4`/`@v5` 浮动引用 | grep | 安全（供应链） |
| 6 | `PRODUCT.md:43` 含特定业务项目名 | grep | 独立性违反 |
| 7 | README 仍写 "release candidate" | grep | 文档错误 |
| 8 | `check_remote_enforcement` 以 PR requirement 为 ENFORCED 充分条件 | 代码审查 | 设计合同不一致（FR-04） |
| 9 | `status --json` 纯本地（`remote=None`） | 代码审查（`main.py:572`） | 设计合同不一致（FR-03） |
| 10 | 模板 workflow 用 `@v1.0.0` 浮动 tag 装 CLI | grep | 安全（FR-05：应优先固定 SHA） |
| 11 | AT-01 失败时 `skip` 当通过 | 代码审查 | 测试设计缺陷（FR-01） |

## 权限边界（实证）

- PAT 可读双仓、可写 LACD（除 workflows/）。
- `.github/workflows/*` 写 → 403（缺 workflow scope），tree 层即拒。
- Branch protection / ruleset 明细 → 403（缺 administration）。
- 采用者仓：仅只读审计，不做任何写入（Phase 2 门槛未过）。

## 结论

v1.0.1 的 P0 工作 = 上表 11 项缺陷 + FR-01..FR-07 + DISC-01..DISC-05。
`wheel 缺 templates` 是最高优先级（新用户完全不可用）。

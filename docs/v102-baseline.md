# v1.0.2 P0 基线证据（2026-10-12）

## 仓库状态（fresh read）

| 事实 | 证据 | 等级 |
|---|---|---|
| LACD `main` HEAD | `56dcfd2e`（PR #2 merge，v1.0.1） | VERIFIED |
| tag `v1.0.1` → `56dcfd2e` | `GET /git/refs/tags/v1.0.1` | VERIFIED |
| Release v1.0.1 已发布 | 非草稿；bundle sha256 `a01c9e4f…e7e7ec51`；notes 含闭合 JSON + SHA 行（08:46 UTC 修订） | VERIFIED |
| CI | 3.11/3.12 各 `370 passed, 1 skipped`，覆盖率 89.06% | VERIFIED |
| 采用者仓 `main` | `4219961c2640`，`protected:true`（只读，未动） | VERIFIED |

## P0 缺陷复现

| # | 缺陷 | 复现 | 分类 |
|---|---|---|---|
| 1 | 模板 `lacd-verify.yml:45` 装 `@v1.0.0` 却调 `--local` | v1.0.0 CLI 无 `--local` 参数 → 采用者 CI 必 fail | 代码缺陷（P0） |
| 2 | `_remote_dimension()` 传 `policy=None` | `main.py:661` 实证；严格 `EnforcementPolicy` 存在但未进 CLI 主路径 | 设计未落地（P0） |

## Q2 语义核对

- 依据：任务规格 §0（引 Notion S0/S4）+ S0 页面 fresh read（2026-10-12）。
- Q1 = 独立规格/验收审查（开发前）；Q2 = 独立 Web AI 审 PR 最终稳定 HEAD（Human 合并前）；
  RRC = 合并后发布资格核验（非 Q2）；PRV = 发布后验证。
- 不恢复 `mode2-review` JSON 评论机器门禁、`mode2-freeze` HTML 解析、coordinator。

## 权限边界

- PAT：LACD 可写（除 workflows/）；protection 明细 403；采用者仓只读。
- Q2 独立审查：优先浏览器 Web AI（授权交互），Cloudflare/验证即停，转 Human 手工中继。

# ADR-0009：`verify --local` / `--remote` 分层

- 状态：已接受（2026-10-12，v1.0.1 Freeze）
- 背景：v1.0.0 的 `verify` 在无远端权限环境（如采用者 PR CI）下，
  远端维度永久 `unverified`，`--strict` 永久红灯——采用者无法得到"本地绿"。
  同时 `status --json` 纯本地计算，与用户"远端已确认"的预期不一致。

## 决定

1. 新增 `--local`：跳过一切远端查询，`remote_enforcement=unverified/LOCAL_ONLY`；
   采用者 CI 默认用此模式。`--local` 与 `--remote` 互斥。
2. 不带 flag 时保持 v1.0.0 行为（尝试远端、失败降级），向后兼容。
3. `status --json` 增加 `dimensions.remote_enforcement.query`（`live|not_queried`），
   明确哪些维度是实时查询、哪些是未查询；`status` 本身永不触发网络。

## 替代方案

- 让 CI 传 token 读保护 API：被否决——把管理员级权限下放给 PR CI，
  违背最小权限；且 fork PR 根本拿不到 token。
- `--strict` 在 unverified 时直接过：被否决——会把"没查"混同为"通过"，
  破坏 fail-closed。

## 风险与回退

- 用户可能误用 `--local --strict` 当成"全绿"：文档与输出中明确标注
  `LOCAL_ONLY`，`status` 的 `query` 字段机器可读，审计可查。

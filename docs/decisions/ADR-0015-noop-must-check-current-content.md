# ADR-0015：noop 判定必须先检查当前文件内容

- 状态：已接受（2026-10-16，v1.0.3 Freeze，HD-05）
- 背景：v1.0.2 的 `_iter_ops` 在 `old == new_sha`（锁基线 hash 等于新模板 hash，
  即模板在版本间未变）时直接判 noop，**不检查磁盘当前内容**。若用户已漂移文件
 （`cur != old`），该分支会把已漂移的文件误判为"干净"，掩盖真实 DRIFT，
  违背 fail-closed 原则。

## 决定

1. 三方比较的判定顺序改为**当前内容优先**：`cur == new` → noop；
   `cur == old`（且 `old != new`）→ update；否则 → conflict。
   `old == new` 不再是独立的 noop 短路条件（当 `old == new` 且磁盘干净时，
   `cur == new` 自然成立，语义等价）。
2. 适用于受管文件（replace）与共享文件（marked-section）两条路径。
3. 本变更触及冻结合同 `docs/architecture/MODULE_CONTRACT.md` §5.3 的
   `cur==new or old==new -> noop` 表述，已随 v1.0.3 Freeze 一并批准更新。

## 替代方案

- 保持旧语义、仅文档声明残余风险：被否决——DRIFT 掩盖是 fail-closed 缺口，
  不是可接受的文档化残余；Human 裁定修复（HD-05）。
- 在 `old == new` 分支内补 `cur` 检查但保留分支结构：被否决——等价但冗余；
  当前内容优先的顺序更简洁且覆盖全部组合。

## 风险与回退

- 行为变更仅影响"模板未变但文件已漂移"的判定（旧行为 noop → 新行为
  conflict）；干净场景的 noop/update 语义不变（回归测试覆盖）。
- 若未来发现误报，沿用标准 ADR 流程修订，不静默回退。

# ADR-0010：版本化 EnforcementPolicy 与 ENFORCED 严格语义

- 状态：已接受（2026-10-12，v1.0.1 Freeze）
- 背景：v1.0.0 的 `check_remote_enforcement` 以"PR requirement 存在"为
  ENFORCED 充分条件——LACD 自身就是反例（`protected:true` 但 required checks
  为空）。"有 PR 要求就算全绿"是危险的假阳性。

## 决定

1. 引入 `EnforcementPolicy(policy_version="1")`：要求的 checks 列表、PR 要求、
   bypass 容忍度、`human_merge_required`。
2. 判定规则：**全部策略项被证实 → ENFORCED**；任一关键项不可核验（403/404/
   字段缺失）→ UNVERIFIED + 缺失清单；明确不满足 → NOT_ENFORCED。
3. classic protection 与 rulesets 分别核验；ruleset 须 `active` 且 ref 条件匹配。
4. 旧无 policy 调用保留但标记 deprecated，文档注明其不充分性（向后兼容，不静默改变语义）。

## 替代方案

- 直接改掉旧函数语义：被否决——v1.0.0 已发布，静默收紧语义会让旧调用者的
  "ENFORCED" 历史结论失去可比性；显式 policy 版本更诚实。

## 风险与回退

- 策略配错（如 check 名写错）会导致 UNVERIFIED 而非 ENFORCED——这是**设计意图**
  （fail-closed），文档中明确说明这是安全特性不是 bug。

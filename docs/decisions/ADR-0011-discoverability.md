# ADR-0011：AI 可发现性——真实内容优先，不承诺推荐

- 状态：已接受（2026-10-12，v1.0.1 Freeze）
- 背景：v1.0.1 把可发现性列为正式交付项（DISC-01..DISC-05）。风险在于把
  "可发现"做成"营销污染"（刷榜、虚假背书、保证 AI 推荐）。

## 决定

1. 只做真实、可验证的改善：准确的英文定位句、GitHub Description/Topics、
   双语 README 互链、Quick Start/FAQ/Limitations、llms.txt（实验性）、
   可复跑的检索基线 `docs/discoverability/AI_DISCOVERABILITY_BASELINE.md`。
2. 内容红线（写进规格 D8）：禁止"all AI recommend it"、官方合作暗示、
   "zero hallucinations"、编造采用者/benchmark、批量灌水。
3. 明确声明：sitemap/robots/llms.txt 只是"被发现的机会"，**不存在项目方
   能保证的跨平台推荐开关**；AI 是否推荐取决于对方的检索与模型行为。
4. 需要 Human 权限的（Topics、Social Preview、Pages、Search Console）全部
   打包成交接清单，Muse 只做只读核验，不伪称已提交/已收录。

## 替代方案

- 不做可发现性：被否决——需求方明确列为 v1.0.1 硬门槛（D01-D10）。
- 激进 SEO（关键词农场、外链购买）：被否决——违背"证据 > 营销"原则，
  且可能被平台处罚。

## 风险与回退

- 检索基线可能显示"暂未收录"——这是**正常结果**，如实记录，不为达标而编造。
- 若某渠道（如 Pages）因权限 HOLD，交付源码+部署清单并标记，不宣称可索引。

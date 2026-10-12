# ADR-0013：采用者模板的版本固定策略

- 状态：已接受（2026-10-12，v1.0.2 Freeze）
- 背景：v1.0.1 模板装 `@v1.0.0` 却调 `--local`（v1.0.0 无此 flag）→
  采用者 CI 必 fail。P0 缺陷。

## 决定

1. 模板 `pip install` 行使用与模板 `template_version` 一致的 tag
   （`git+https://github.com/SkyCjq/LACD.git@vX.Y.Z`）。
2. 不得预先编造尚未产生的合并 SHA；tag 的不可变性由"Human 打 tag +
   RRC/PRV 核验 tag→SHA"保证，文档明示此信任链。
3. 同项目 `lock.json` 的版本与模板安装版本不一致 → `verify` 非零错误
  （fail-closed，不静默混用）。

## 替代方案

- 固定 40 位 commit SHA：被否决——模板发布时未来版本的 SHA 尚不存在，
  只能事后物化；当前两阶段发布流程下 tag 是务实选择。
- `pipx` 浮动最新版：被否决——破坏可复现性，违背版本锁定原则。

## 风险与回退

- tag 被移动的风险：由 RRC（tag→SHA 核验）+ PRV（发布后复核）覆盖；
  发现 tag 漂移 → HOLD 发布/采用。

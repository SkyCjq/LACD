# ADR-0004：锁文件 `.lacd/lock.json`

- 状态：已接受（2026-10-12）
- 背景：契约建议 `.lacd/lock.json`，允许改名但须说明。
- 决定：采用默认名 `.lacd/lock.json`，不改名；同目录下放
  `project-rules.yaml`（项目扩展）、`backup/`（迁移备份）。
- 理由：与契约示例一致，减少心智负担；`.lacd/` 集中隔离 LACD 状态，
  避免污染项目根目录。

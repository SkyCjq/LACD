# ADR-0002：无中央登记数据库；更新源可插拔

- 状态：已接受（2026-10-12）
- 背景：契约要求"无需中央登记数据库"；测试需要离线可复现的更新源。
- 决定：`UpgradeSource` 抽象；生产用 `GitHubReleasesSource`（GitHub Releases API）；
  测试/离线用 `FileSource`（本地 JSON）。采用状态只存于采用者仓库。
- 后果：`upgrade --check` 的"可信"完全来自源的可配置性与校验（bundle sha256），
  不依赖任何中心服务在线。

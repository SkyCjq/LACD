# 故障处理

| 现象 | 处理 |
|---|---|
| `upgrade --check` 报源不可达 | 检查网络/代理；`lacd doctor` 看连通性分项；离线用 `--template-dir` |
| 迁移报 conflict | 按报告手动解决后重跑；备份在 `.lacd/backup/<ts>/` |
| `status` 显示 DRIFTED | `lacd verify` 看漂移文件；确认为有意改动则重跑 `init --force` 重建基线（备份旧文件） |
| `verify --strict` 退出 2 | 按输出的原因码处理；远端 UNVERIFIED 见下 |
| 远端 enforcement 显示 unverified | 需要 GitHub 凭证（`gh auth` 或 `GITHUB_TOKEN`）与读取分支保护的权限；无权限时如实保持 unverified |
| CLI 版本与锁版本不一致 | 用项目隔离 venv 安装锁定的精确版本（见 install.md） |

# 接入（adopt）

```bash
cd your-project
lacd init --dry-run   # 预览拟写入文件，不写盘
lacd init             # 写入 lacd.yaml / .lacd/lock.json / 受管模板 / AGENTS.md section
lacd status           # 查看采用状态
lacd verify           # 本地校验；远端强制需 GitHub 凭证才会核验
```

- 重复执行 `lacd init` 幂等（第二次无 diff）。
- 已有 `lacd.yaml` 时直接 `init` 会拒绝覆盖；用 `--force` 显式重建（会备份旧文件）。
- `init` 永不 `git add/commit/push`。

# 升级（upgrade）

```bash
lacd upgrade --check                 # 发现可用版本（Patch/Minor/Major 分类）
lacd upgrade --dry-run --to 1.0.1    # 预览变更：不改工作树
lacd upgrade --to 1.0.1              # 执行迁移（备份→三方合并→更新锁）
lacd upgrade --to 1.0.1 --prepare-pr # 生成升级分支并创建 PR（永不自动合并）
lacd upgrade --to 2.0.0 --accept-major  # Major 需显式接受迁移计划
```

升级后核验：`lacd status --json && lacd verify --strict`。
回退：用最近一次 `.lacd/backup/<timestamp>/` 恢复，或走受控降级 PR。
离线/测试：`--template-dir DIR` / `LACD_TEMPLATE_DIR` 指定本地模板包。

# ADR-0003：模板包随 Release 发布，sha256 强制校验

- 状态：已接受（2026-10-12）
- 背景：升级到新版本需要新版受管模板内容；不能"下载后直接执行远端脚本"。
- 决定：每个 Release 附带 `lacd-templates-<version>.zip`，其 sha256 写在
  Release 元数据（`template_bundle` 字段）中；CLI 下载后先验 sha256，
  不一致则 fail-closed。离线场景用 `--template-dir` / `LACD_TEMPLATE_DIR`。
- 后果：发布流程必须生成并上传模板包（见发布检查清单）；v1.0 的 bundle
  即本仓库 `src/lacd/templates/` 打包。

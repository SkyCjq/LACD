# 人工审批点（Human-only）

以下事项必须由 Human 执行，Muse 不得代办：

1. **合并任何 PR**（含升级 PR）：在 GitHub 网页完成 Approve + Merge。
2. **创建 main 分支保护 / Ruleset**（如未由自动化建立）：要求 PR、必需检查。
3. **创建 Release 与 tag**（如 `v1.0.0`）：Release 附带 `lacd-templates-<version>.zip`
   及 sha256，供升级源校验。
4. **PyPI 发布**（如未来决定）：确认包名所有权与发布授权后执行。
5. **Major 升级的迁移计划批准**：`--accept-major` 仍需 Human 先审阅计划。
6. **安全例外**：任何放宽保护、绕过检查的决定。

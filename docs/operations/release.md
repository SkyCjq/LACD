# 发布检查清单（maintainer / Human 执行）

> LACD 的正式发布（tag / Release / 包索引）必须由 Human 授权并执行，
> Muse 不得代办、不得谎称已发布。

## v1.0.0 发布步骤

1. **候选构建验证**
   - [ ] `pytest -q` 全绿（含 acceptance AT-01..AT-25）
   - [ ] `pytest --cov=lacd --cov-fail-under=80 -q` 通过
   - [ ] `pip install .` 在干净 venv 可装，`lacd --version` 输出目标版本
   - [ ] `src/lacd/__init__.py` 的 `__version__`、CHANGELOG、锁默认版本一致
2. **PR 合并**：实现 PR 经 Human Approve + Merge 到 `main`，CI 全绿
3. **打 tag**：`git tag -a v1.0.0 -m "LACD v1.0.0" && git push origin v1.0.0`
   （精确 commit 一经固定即不可变）
4. **模板包**：`python - <<EOF` 打包 `src/lacd/templates/` 为
   `lacd-templates-1.0.0.zip`，计算 sha256
5. **GitHub Release**：以 tag 创建 Release，上传模板包附件，正文写明
   sha256（供 `upgrade` 的 bundle 校验；格式见 `upgrade/source.py` 的
   `_parse_template_bundle` 约定）
6. **分支保护**：确认 `main` 的 Ruleset/Protection 生效（要求 PR；
   CI 落地后加 Required status checks）
7. **公告**：Release notes = CHANGELOG 对应章节

## 发布后

- 采用者用 `lacd upgrade --check` 应能发现新版本
- 保留上一 minor 的支持窗口（见 UPGRADE_CONTRACT §5）

## 不做的事

- 未走完清单不对外宣称"已发布"
- 不发布到 PyPI，除非包名所有权与发布授权已确认（ADR-0006）

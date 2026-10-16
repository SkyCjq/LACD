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
   `parse_template_bundle` 约定，v1.0.1 起支持三种格式）
6. **分支保护**：确认 `main` 的 Ruleset/Protection 生效（要求 PR；
   CI 落地后加 Required status checks）
7. **公告**：Release notes = CHANGELOG 对应章节

## 发布后

- 采用者用 `lacd upgrade --check` 应能发现新版本
- 保留上一 minor 的支持窗口（见 UPGRADE_CONTRACT §5）

## 不做的事

- 未走完清单不对外宣称"已发布"
- 不发布到 PyPI，除非包名所有权与发布授权已确认（ADR-0006）

---

## v1.0.1 Release notes 模板（FR-02）

> Release 正文必须**同时**包含闭合 JSON 块与 `Template-Bundle-SHA256:` 明文行
> （双格式，互相备份；`parse_template_bundle` 按 (a)→(b)→(c) 顺序解析，全部
> fail-closed）。**围栏必须闭合**——v1.0.0 的未闭合围栏导致解析失败，
> 不得重蹈。

```markdown
## LACD vX.Y.Z — <release title>

<CHANGELOG 对应章节内容>

## Template bundle

`lacd-templates-X.Y.Z.zip` — SHA-256 verified by `lacd upgrade` before use:

```json
{"template_bundle": {"url": "https://github.com/SkyOneBranch/LACD/releases/download/vX.Y.Z/lacd-templates-X.Y.Z.zip", "sha256": "<64 hex>"}}
```

Template-Bundle-SHA256: <64 hex>
```

**填写要求**：
- `<64 hex>` 两处必须一致，且等于附件 zip 的真实 sha256（`sha256sum` 实测，
  不得手填）。
- `url` 必须与附件的 `browser_download_url` 一致
  （`https://github.com/SkyOneBranch/LACD/releases/download/vX.Y.Z/lacd-templates-X.Y.Z.zip`）。
- JSON 块的围栏必须闭合（开头和结尾各一行 ` ``` `）。

### 发布校验清单（Human 发布时逐项打勾）

- [ ] 附件名 == `lacd-templates-<version>.zip`，且 `<version>` == Release 的 tag 版本
      （去 `v` 前缀后一致）
- [ ] tag 的 SHA == `main` 合并提交的 SHA（`git rev-parse vX.Y.Z` 核对）
- [ ] 正文 JSON 块中 `sha256` == 附件实测 sha256（下载附件后 `sha256sum`）
- [ ] 正文 `Template-Bundle-SHA256:` 行的 sha256 == 同一值
- [ ] 正文 JSON 块中 `url` == 附件的实际下载 URL
- [ ] 解压附件，`manifest.json` 的 `template_version` == Release 版本，
      且 manifest 内每个文件的 sha256 与解压内容一致
      （`lacd` 的 `load_bundle_from_dir` 会在升级时复验）
- [ ] Release 为正式版（非 draft、非 prerelease），除非有意发预发布

任一项不匹配 → 停止发布，修复后重走清单。**绝不发布 sha256 存疑的模板包。**

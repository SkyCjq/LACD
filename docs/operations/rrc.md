# RRC 运行手册（Release Readiness Check）

> **RRC 是什么**：Human 合并 PR 后、打 Tag 前，对合并 SHA、CI、制品、发布元数据
> 的核验。**RRC 不是 Q2**（Q2 是合并前的独立审查；RRC 是合并后的发布资格核验）。
> 仅 `RRC PASS` + Human Release Authorization → 打 Tag / 发 Release。
>
> **铁律**：RRC 证据**不回写 main**（防 SHA 循环）。RRC 缺关键证据 → `HOLD`，
> 不得打 Tag。

## 前置输入

| 输入 | 来源 | 示例 |
|---|---|---|
| `MERGE_SHA` | PR 页面的 merge commit | `56dcfd2e...`（40 位完整） |
| `PR_URL` | GitHub PR | `https://github.com/SkyCjq/LACD/pull/2` |
| `VERSION` | 本次发布版本 | `1.0.2` |
| Q2 记录 | 独立审查交付 | reviewer/模型/时间/`reviewed_head_sha`/结论原文 |

## 检查清单

### RRC-1. 合并 SHA 核验

```bash
# 1. PR 确实已合并，且 merge_commit_sha == main HEAD
gh api /repos/SkyCjq/LACD/pulls/<N> --jq '{merged, merge_commit_sha}'
gh api /repos/SkyCjq/LACD/branches/main --jq '.commit.sha'
# 断言：merged == true，且两处 SHA 相等（前 8 位对上不够，要全 40 位）
```

| 结果 | 条件 |
|---|---|
| PASS | PR merged=true，`merge_commit_sha` == `main` HEAD（40 位） |
| FAIL | PR 未合并，或 SHA 不一致 → 停止，不得继续 |
| HOLD | API 不可读 → 等 Human 在网页确认 |

### RRC-2. 该 SHA 的 CI 全绿

```bash
# 2. 合并 SHA 的 CI run（注意：看 merge commit 的 run，不是 PR 分支旧 run）
gh api "/repos/SkyCjq/LACD/actions/runs?per_page=5" \
  --jq '.workflow_runs[] | {sha: .head_sha[0:8], event, status, conclusion}'
# 断言：head_sha == MERGE_SHA 的 run，status == completed，conclusion == success
```

| 结果 | 条件 |
|---|---|
| PASS | MERGE_SHA 的 push run 全绿（含 coverage 门槛） |
| FAIL | 任一 required job 红 → 停止 |
| HOLD | run 还在跑 → 等；API 不可读 → Human 网页确认 |

### RRC-3. Required Checks 生效证据

```bash
# 3. 分支保护要求了哪些 checks（需要 admin 权限；403 则转 HOLD）
gh api /repos/SkyCjq/LACD/branches/main/protection/required_status_checks
# 断言：checks 列表与 CI 的 job 名一致（如 "test (3.11)" / "test (3.12)"），
# 且 RRC-2 的绿 run 确实覆盖了这些 checks
```

| 结果 | 条件 |
|---|---|
| PASS | required checks 非空，且 MERGE_SHA 的 run 覆盖全部 |
| FAIL | checks 为空但策略要求非空 → 保护未生效，停止 |
| HOLD | 403 不可读 → Human 在 Settings → Branches 网页截图确认 |

**不得**因一个 API 的 403 推断"保护不存在"或"保护生效"。

### RRC-4. Q2 记录检查

人工核对（不做机器门禁）：

- [ ] Q2 结论原文存在（reviewer/模型/时间可查）
- [ ] `reviewed_head_sha` == 本次 PR 合并前的最终 HEAD（若 PR 合并后有新 push，Q2 已过期）
- [ ] 结论为 PASS（FAIL/HOLD → 不得发布）

| 结果 | 条件 |
|---|---|
| PASS | Q2 PASS 且 `reviewed_head_sha` 未过期 |
| FAIL | Q2 FAIL → 停止 |
| HOLD | Q2 HOLD 或记录缺失 → 等独立审查 |

### RRC-5. 制品摘要核验（wheel / zip / manifest）

```bash
# 4. 从 MERGE_SHA 构建 wheel，断言模板/版本一致
git clone https://github.com/SkyCjq/LACD.git rrc-build && cd rrc-build
git checkout <MERGE_SHA_FULL>
pip wheel --no-deps . -w /tmp/rrc-wheel
python3 - <<'EOF'
import zipfile, glob, json, hashlib
whl = glob.glob("/tmp/rrc-wheel/*.whl")[0]
names = zipfile.ZipFile(whl).namelist()
assert any("templates/workflows" in n for n in names), "wheel 缺 templates"
assert any("manifest.json" in n for n in names), "wheel 缺 manifest"
print("wheel OK:", whl)
EOF

# 5. 构建模板包，计算 sha256（此值将用于 Release notes）
cd rrc-build && python3 - <<'EOF'
import zipfile, os, hashlib, json
tpl = "src/lacd/templates"
man = json.load(open(f"{tpl}/manifest.json"))
for e in man["files"]:
    h = hashlib.sha256(open(os.path.join(tpl, e["path"]), "rb").read()).hexdigest()
    assert h == e["sha256"].lower(), f"manifest 假哈希: {e['path']}"
out = "/tmp/rrc-bundle.zip"
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for root, _, fns in os.walk(tpl):
        for fn in sorted(fns):
            full = os.path.join(root, fn)
            z.write(full, os.path.relpath(full, tpl))
print("bundle sha256:", hashlib.sha256(open(out, "rb").read()).hexdigest())
print("bundle size:", os.path.getsize(out))
EOF
```

| 结果 | 条件 |
|---|---|
| PASS | wheel 含 templates+manifest；manifest 哈希逐文件为真；bundle sha256 已记录 |
| FAIL | 任一断言失败 → 停止，修代码重新走 PR（不得手工改制品） |
| HOLD | 构建环境不可用 → 换环境重跑 |

### RRC-6. Release notes 可解析性（预检）

在 Human 写 Release notes **前**，用本次模板预检：

```bash
# 6. 模板必须同时含：闭合 ```json 块 + Template-Bundle-SHA256: 行
# 用 parse_template_bundle 对模板做解析测试（见 docs/operations/release.md）
python3 - <<'EOF'
import sys; sys.path.insert(0, "rrc-build/src")
from lacd.upgrade.source import parse_template_bundle
notes = open("release-notes-draft.md").read()  # Human 按模板起草的正文
parsed = parse_template_bundle({"body": notes, "assets": [
    {"name": "lacd-templates-<VERSION>.zip",
     "browser_download_url": "https://github.com/SkyCjq/LACD/releases/download/v<VERSION>/lacd-templates-<VERSION>.zip"}]})
assert parsed is not None, "Release notes 无法解析出 bundle（参考 v1.0.0 未闭合围栏事故）"
assert len(parsed["sha256"]) == 64
print("notes parseable:", parsed["url"])
EOF
```

| 结果 | 条件 |
|---|---|
| PASS | 模板解析成功，URL 与 sha256 格式正确 |
| FAIL | 解析为 None → 修正 notes 模板，重新预检 |
| HOLD | —（本检查无外部依赖，不应 HOLD） |

## 结果汇总与放行规则

| RRC 总结果 | 条件 | 后续 |
|---|---|---|
| **PASS** | RRC-1..6 全部 PASS | → 等 Human Release Authorization → 打 Tag / 发 Release |
| **FAIL** | 任一 FAIL | 停止。修代码 → 新 PR → 新 Q2 → 新 RRC（不得跳过） |
| **HOLD** | 任一 HOLD，无 FAIL | 等待阻塞解除；不得降级为 PASS |

## 证据存放

- RRC 检查输出保存为本地文件（如 `rrc-<VERSION>-<MERGE_SHA8>.md`），**不提交到 main**。
- 随发布包归档到 `~/workspace/lacd-delivery/`（或 Human 指定的外部位置）。
- 报告格式：每项 `ID / 命令 / SHA / 结果 / 证据摘要 / 未验证原因`。

## 与 Q2/PRV 的关系

```
PR 最终 HEAD → Q2 独立审查 (PASS) → Human Merge → RRC (本手册) →
Human Release Authorization → Tag + Release → PRV（发布后验证）→ 生产采用
```

- Q2 过期（HEAD 变化）→ 重审 Q2，RRC 不得放行。
- PRV 失败 → 禁止生产采用（见采用手册）。

# ADR-0008：wheel 打包模板资源与 importlib.resources 定位

- 状态：已接受（2026-10-12，v1.0.1 Freeze）
- 背景：v1.0.0 的 wheel 不含 `templates/`（`package-data` 只声明了 schemas），
  干净安装后 `lacd init` 报 `FileNotFoundError`——新用户完全不可用（P0 阻断）。
  另有 `Path(__file__)` 硬编码，editable/源码树/wheel 三态行为不一致。

## 决定

1. `pyproject.toml` 的 `package-data` 显式包含 `lacd/templates/**` 与
   `lacd/schemas/*.json`；`src/lacd/templates/` 加 `__init__.py` 成为正式子包。
2. 新增唯二资源入口 `lacd.cli.templates_dir()` 与 `lacd.config.schemas_dir()`，
   内部用 `importlib.resources.files()`；全仓禁止 `Path(__file__)` 定位资源。
3. `build/`、`.coverage` 等生成物 `git rm --cached`，CI 加 `git ls-files` 断言。

## 替代方案

- 运行时从 GitHub 下载模板：被否决——违背"离线可用"与"不执行远端内容"原则，
  且安装时无网络即不可用。
- 继续用 `Path(__file__)` + 指望 setuptools 行为：被否决——三态不一致已实证踩坑。

## 风险与回退

- `importlib.resources.files()` 需 Python 3.9+；本项目要求 3.11+，无风险。
- 若某打包后端行为异常，回退：在 `pyproject.toml` 改用 `setuptools` 的
  `data-files` 显式清单（需同步改两处入口做兼容）。

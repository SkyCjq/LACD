# ADR-0006：v1.0 不发布 PyPI；安装走可复现的 git 源

- 状态：已接受（2026-10-12）
- 背景：契约要求"确认 PyPI 包名所有权、发布权限与发布授权之后"才可写
  `pipx install lacd`；当前三者皆无。
- 决定：v1.0 不发布 PyPI；文档只给出**已实测**的替代安装命令：
  `pip install git+https://github.com/SkyCjq/LACD.git@<tag/sha>` 与本地
  `pip install .`；`pipx install lacd` 不得出现在 v1.0 文档中。
- 后果：安装可复现性依赖 git tag/sha 的不可变性；锁文件的 `source.type=git-tag`
  记录精确 commit。

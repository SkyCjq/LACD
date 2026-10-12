# ADR-0001：Python 3.11+，最小第三方依赖

- 状态：已接受（2026-10-12）
- 背景：需要一个可独立安装、长期可维护的 CLI；避免依赖地狱与供应链风险。
- 决定：Python 3.11+；`src/` 布局；`pyproject.toml`；运行时第三方依赖仅
  `pyyaml`、`jsonschema`；CLI 用标准库 `argparse`（不用 click/typer）。
- 理由：argparse 零依赖且足以表达冻结的命令面；pyyaml/jsonschema 是
  YAML 配置与 JSON Schema 校验的事实标准，无更轻的等价替代。
- 后果：命令帮助文本需手写保持一致；后续如命令面大幅扩张可重审。

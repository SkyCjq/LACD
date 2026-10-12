# 安装（v1.0）

> v1.0 未发布 PyPI。以下命令均已实测（见验收报告）。

## 从 git 安装（推荐，可复现）

```bash
pip install "git+https://github.com/SkyCjq/LACD.git@v1.0.0"
# 或固定到精确 commit：
pip install "git+https://github.com/SkyCjq/LACD.git@<40位commit SHA>"
```

## 本地安装（开发）

```bash
git clone https://github.com/SkyCjq/LACD.git && cd LACD
pip install .
```

## 项目隔离执行（多版本共存）

```bash
python -m venv .venv-lacd-1.0.0 && . .venv-lacd-1.0.0/bin/activate
pip install "git+https://github.com/SkyCjq/LACD.git@v1.0.0"
lacd status --json
```

验证：`lacd --version` 应输出 `1.0.0`。

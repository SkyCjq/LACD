# LACD｜轻量智协开发

[English](README.md) · [GitHub Releases](https://github.com/SkyOneBranch/LACD/releases)

**你提需求，AI Agent 写代码；测试和独立审查帮助发现问题，最后由你决定是否合并。**

LACD（Lightweight Agentic Collaborative Development）是一个开源的 AI 协作开发治理框架。它帮助你在 GitHub 上组织需求、检查代码变更、保留证据，并安全地采用和升级开发规则。**LACD 不是 AI 编程模型，也不自带 Muse、ChatGPT 或其他 AI 服务。**

你可以选择不安装本地 IDE，使用云端 AI Agent 和 GitHub 开发；但 Python、Git、CI 和实际运行环境仍然必不可少。

<img width="1774" height="887" alt="LACD Workflow Infographic Poster" src="https://github.com/user-attachments/assets/e2d6db18-e33a-4434-b3ec-6f4fc1f16c96" />

## 像产品经理一样，让 AI 帮你开发

假设你想做一个任务管理工具：

1. **你提出需求**：例如“可以添加任务、标记完成”，并说明怎样算完成。
2. **AI Agent 开发**：Muse 或其他具备相应能力的 Agent 编写代码，在 GitHub 提交 Pull Request（PR，代码修改申请）。
3. **自动测试**：GitHub Actions 执行测试；有问题就修改，再测试。
4. **独立 AI 审查**：与开发者隔离的审查者检查 PR 最终代码版本及证据，发现问题就退回修复。
5. **你最终验收**：了解测试结果和剩余风险，决定是否合并。合并不等于自动部署上线。

**简单说：AI 负责做，测试与独立审查负责找问题，Human（你）负责拍板。**

复杂任务还可以在开发前加入独立规格评审（Q1）和需求冻结（Freeze）。这是推荐的治理做法，**不是 LACD CLI 对每个项目自动强制的步骤**。

## AI 写代码不难，难的是判断它写得对不对

AI 可以很快生成代码，却仍可能误解需求、漏掉测试，或影响原来正常的功能。LACD 帮助你把开发过程变得更清楚、可检查：

- **有据可查**：知道改了什么、测试了什么，审查对应哪个 Git commit。
- **问题可以打回修复**：测试失败或审查发现问题，不应直接合并。
- **采用和升级可控**：`lacd init` 初始化项目规则；`lacd verify` 检查状态；`lacd upgrade` 提供升级检查和预览，遇到冲突不应静默覆盖你的修改。
- **决定权在人**：LACD 不会替你批准 PR、自动合并或发布版本。

这些措施能**降低风险，但不能保证代码零缺陷**。独立 AI 审查也不能只是开发 Agent 在原会话里给自己打分。

## 灵活选 Agent，按实际额度控制成本

你可以按任务和可用能力选择 Muse、其他编码 Agent，或使用适合审查代码的独立 Web AI（例如 ChatGPT）。开发者和审查者可以使用不同工具，并不要求固定一家厂商。

部分服务可能提供免费额度，适合低成本尝试；**额度、权限、价格及能否访问 GitHub 由各服务商决定**。LACD 是开源框架，不提供 AI 账号、免费额度，也不保证所有 Agent 都具备相同能力或审查质量。

## 快速开始

**准备：** Python 3.11+、Git、一个现有的 Git 项目；若使用完整 GitHub PR 流程，还需要 GitHub 仓库及相应权限。建议在独立的 Python 虚拟环境中安装。

下面安装**当前正式发布的 v1.0.3**（[查看 Releases](https://github.com/SkyOneBranch/LACD/releases)）。请在项目目录运行：

```bash
python -m pip install "git+https://github.com/SkyOneBranch/LACD.git@v1.0.3"
lacd --version
lacd init --dry-run
lacd init
lacd status --json
lacd verify --local --strict
```

`init --dry-run` 先预览，`init` 再写入项目配置和受管模板。**先阅读预览，再确认是否适合你的仓库。**

`verify --local --strict` 只验证本地条件，**不能证明 GitHub 分支保护已生效**。如需检查远端保护，请阅读[采用指南](docs/operations/adopt.md)与[治理协议](docs/specification/PROTOCOL.md)。

如果你的项目使用过旧版 LACD，版本锁中可能仍保留原来的上游仓库名称。这是真实的历史来源信息，不应手工改写。需要升级时，请参考[升级指南](docs/operations/upgrade.md)。

## 安全边界与更多资料

- **LACD 不替你写代码，也不自动合并、管理密钥或发布 Release。**
- **CI 通过 ≠ GitHub Required Checks 已被强制配置。** 对无法核实的远端状态，应如实标记为未验证。
- **独立 AI 审查 ≠ 开发者自检。** 审查应与实现过程隔离，并对应 PR 最终版本；Human 合并也不自动等于存在 GitHub 原生 `APPROVED` 记录。
- **项目仍属于你。** 改动受管文件或升级前，应先查看差异、处理冲突，保留可回退的变更。

继续阅读：[采用与初始化](docs/operations/adopt.md) · [安全升级](docs/operations/upgrade.md) · [协议说明](docs/specification/PROTOCOL.md) · [升级契约](docs/specification/UPGRADE_CONTRACT.md) · [Agent 接入指引](docs/agents/) · [贡献指南](CONTRIBUTING.md)

**LACD 的目标不是让 AI 永远不犯错，而是让你更容易发现错误、要求修复，并掌握最终决定权。**

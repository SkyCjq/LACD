# LACD v1.0 模块接口契约（冻结版，2026-10-12）

> 本文档是所有实现子任务必须遵守的接口契约。模块内部实现可自由取舍，
> 但**跨模块的函数签名、数据结构、文件格式、退出码必须与本文一致**。
> 任何偏离必须先更新本文并说明理由（ADR）。

## 0. 全局约定

- Python 3.11+，`src/` 布局，包名 `lacd`，`__version__ = "1.0.0"` 在 `src/lacd/__init__.py`。
- 第三方运行时依赖仅：`pyyaml`、`jsonschema`。CLI 用标准库 `argparse`。
- 所有面向用户的错误信息：英文为主（CLI 默认英文），关键信息可附中文；文档中英双语。
- 所有路径操作限制在 `project_dir` 内；拒绝绝对路径逃逸与 `..` 穿透（fail-closed）。
- 任何函数不得输出秘密值（token、密钥）；`status --json` 必须不含秘密。
- 时间统一用 UTC ISO-8601（`datetime.now(timezone.utc).isoformat()`）。

## 1. 目录与模块划分

```text
src/lacd/
├── __init__.py            # __version__ = "1.0.0"
├── cli/main.py            # main(argv: list[str] | None) -> int；argparse 装配
├── core/state.py          # AdoptionState, compute_state(), StateReport
├── core/report.py         # build_status_dict(), validate_status_dict()
├── core/versioning.py     # parse_version(), compare(), classify_bump()
├── config/schema.py       # load_config(), validate_config_dict()
├── config/models.py       # LacdConfig dataclass（from_dict/to_dict）
├── config/lockfile.py     # LockFile dataclass：load/save/validate/drift 检查
├── governance/risk.py     # classify_change(paths: list[str]) -> str ("T0"/"T1"/"T2")
├── agents/protocol.py     # read_project_instructions(), agent_checklist()
├── github/remote.py       # check_remote_enforcement() -> EnforcementReport
├── github/pr.py           # prepare_upgrade_pr(), find_existing_upgrade_pr()
├── upgrade/source.py      # UpgradeSource（github-releases / file://）
├── upgrade/compat.py      # CompatibilityPolicy：patch/minor/major 规则与支持窗口
├── upgrade/migrate.py     # 三方合并引擎：plan_migration(), apply_migration(), rollback
└── upgrade/planner.py     # build_upgrade_plan()：dry-run 列表、风险、人工检查点
```

## 2. 核心数据结构

### 2.1 `lacd.yaml`（配置 Schema 版本 "1"）

```yaml
schema_version: "1"            # 必需，字符串
lacd:
  enabled: true               # 必需，bool
  mode: "lacd"                # 必需，枚举 ["lacd"]
  policy_version: "1"         # 必需，字符串
  upstream_repo: "SkyCjq/LACD"  # 可选，默认 "SkyCjq/LACD"
governance:                   # 必需
  pull_request_required: true # 必需，bool
  human_merge_required: true  # 必需，bool
  risk_tiers: ["T0", "T1", "T2"]  # 必需，非空字符串数组
verification:                 # 必需
  bind_to_commit_sha: true    # 必需，bool
  fail_closed: true           # 必需，bool
  github_enforcement_required: true  # 必需，bool
agents:                       # 必需
  protocol: "lacd"            # 必需，枚举 ["lacd"]
  auto_detect: true           # 必需，bool
project:                      # 必需
  extensions: ".lacd/project-rules.yaml"  # 必需，字符串（相对路径）
```

JSON Schema 文件：`schemas/lacd-config.schema.json`（`$id` 含 `lacd-config/1`）。
`config/schema.py::load_config(project_dir) -> LacdConfig`：文件缺失抛
`ConfigNotFound`；非法抛 `ConfigInvalid(reason)`。`validate_config_dict(d) -> list[str]`
返回错误列表（空表合法），供测试与 doctor 使用。

### 2.2 `.lacd/lock.json`（锁格式版本 "1"）

```json
{
  "lock_format_version": "1",
  "lacd_cli_version": "1.0.0",
  "source": {"type": "git-tag", "repo": "SkyCjq/LACD", "ref": "v1.0.0", "commit": "<40位hex或null>"},
  "schema_version": "1",
  "policy_version": "1",
  "template_version": "1.0.0",
  "managed_files": [
    {"path": ".github/workflows/lacd-verify.yml", "sha256": "<hex>",
     "template": "workflows/lacd-verify.yml", "template_version": "1.0.0",
     "merge": "replace"}
  ],
  "shared_files": [
    {"path": "AGENTS.md", "section": "lacd-agent-protocol",
     "sha256": "<受管section内容hash>", "template": "managed/agents-md.section.md",
     "template_version": "1.0.0", "merge": "marked-section"}
  ],
  "generated_at": "<UTC ISO-8601>",
  "generated_by": "lacd 1.0.0"
}
```

`source.type` 枚举：`["git-tag", "git-commit", "local-dir"]`。
`merge` 枚举：`["replace", "marked-section"]`。
`config/lockfile.py::LockFile`：`load(project_dir)`, `save(project_dir)`,
`validate() -> list[str]`，`check_drift(project_dir) -> list[DriftEntry]`，
其中 `DriftEntry = {path, expected_sha256, actual_sha256|null(missing), kind}`。

### 2.3 状态机（`core/state.py`）

```python
class AdoptionState(str, Enum):
    NOT_INSTALLED = "NOT_INSTALLED"
    DECLARED = "DECLARED"
    VALIDATED = "VALIDATED"
    ENFORCED = "ENFORCED"
    DRIFTED = "DRIFTED"
    BLOCKED = "BLOCKED"
    UNVERIFIED = "UNVERIFIED"

@dataclass
class DimensionResult:
    status: str          # "pass" | "fail" | "unverified" | "clean" | "drifted" | "none" | "not-run"
    reasons: list[str]    # 机器可读原因码，如 "CONFIG_INVALID", "REMOTE_PERMISSION_DENIED"

@dataclass
class StateReport:
    state: AdoptionState
    local_validation: DimensionResult
    remote_enforcement: DimensionResult
    drift: DimensionResult
    blocking: DimensionResult
    version_consistency: dict   # {"status": "consistent"|"mismatch"|"unknown", "detail": str}
    next_steps: list[str]

def compute_state(project_dir: str | Path,
                  remote: DimensionResult | None = None) -> StateReport:
    """纯本地计算。若 remote 为 None，remote_enforcement 维度为 unverified/REMOTE_NOT_CHECKED。
    优先级（从高到低）：
      1. 无 lacd.yaml                    -> NOT_INSTALLED
      2. 配置非法 / 锁文件损坏           -> BLOCKED（blocking.reasons 非空）
      3. 无法建立基本事实（非 git 仓库且需要 SHA 等）-> UNVERIFIED
      4. 配置合法但无 lock.json          -> DECLARED
      5. 本地校验失败                    -> DECLARED（local_validation=fail，state 保持 DECLARED 表示"声明未兑现"）
      6. blocking.reasons 非空           -> BLOCKED
      7. drift 非空                      -> DRIFTED
      8. remote 明确 enforced            -> ENFORCED
      9. 其他                            -> VALIDATED
    注意：remote 仅为 unverified 时 state 保持 VALIDATED（AT-06：不伪称 ENFORCED）。
    """
```

原因码（非穷举，实现可增但必须文档化）：
`CONFIG_MISSING`, `CONFIG_INVALID`, `LOCK_MISSING`, `LOCK_CORRUPT`,
`LOCAL_CHECKS_FAILED`, `DRIFT_DETECTED`, `REMOTE_ENFORCED`, `REMOTE_NOT_ENFORCED`,
`REMOTE_PERMISSION_DENIED`, `REMOTE_NO_CREDENTIAL`, `REMOTE_NETWORK_ERROR`,
`REMOTE_NOT_CHECKED`, `VERSION_MISMATCH`, `NOT_A_GIT_REPO`,
`NO_GITHUB_REMOTE`（有 git 仓库但无指向 GitHub 的 origin；2026-10-12 集成增补，
替代此处误导性的 `NOT_A_GIT_REPO` 用法）、`RULESET_PULL_REQUEST_VERIFIED`、
`EXTENSIONS_MISSING`, `EXTENSIONS_PATH_UNSAFE`。

共享文件（`shared_files`，如 AGENTS.md）的漂移哈希语义：取 markers
`<!-- LACD:MANAGED:BEGIN ... -->` 与 `<!-- LACD:MANAGED:END -->` 之间
**原始文本、不做 trim** 的 sha256（`init` 写入与 `check_drift` 读取必须用
同一语义；见 `config/lockfile.py` 的 `extract_managed_section`）。

### 2.4 status JSON（`core/report.py`）

`build_status_dict(project_dir, remote=None) -> dict` 字段（全部必需）：

```json
{
  "schema": "lacd-status/1",
  "lacd_cli_version": "1.0.0",
  "generated_at": "<UTC ISO-8601>",
  "project_dir": "<绝对路径>",
  "branch": "<当前分支或null>",
  "head_sha": "<40位hex或null>",
  "mode": "lacd",
  "versions": {"config_schema": "1", "policy": "1", "lock_format": "1",
               "locked_cli": "1.0.0|null", "template": "1.0.0|null"},
  "state": "VALIDATED",
  "dimensions": {
    "local_validation": {"status": "pass", "reasons": []},
    "remote_enforcement": {"status": "unverified", "reasons": ["REMOTE_NOT_CHECKED"]},
    "drift": {"status": "clean", "reasons": []},
    "blocking": {"status": "none", "reasons": []}
  },
  "version_consistency": {"status": "consistent", "detail": "..."},
  "next_steps": ["..."]
}
```

`validate_status_dict(d) -> list[str]` 用 `schemas/status.schema.json` 校验。
`version_consistency`：比较运行 CLI 版本 vs `lock.lacd_cli_version`，
不一致 → `{"status": "mismatch", ...}` 并在 `next_steps` 给出项目隔离执行建议
（AT-09）。无锁 → `unknown`。

### 2.5 版本号（`core/versioning.py`）

```python
def parse_version(s: str) -> tuple[int, int, int]:   # "1.0.0" -> (1,0,0)；非法抛 ValueError
def compare(a: str, b: str) -> int:                   # -1/0/1
def classify_bump(from_v: str, to_v: str) -> str:     # "patch"|"minor"|"major"|"same"|"downgrade"
def is_supported(version: str, policy: "CompatPolicy") -> bool  # 见 5.2
```

## 3. CLI 契约（`cli/main.py`）

命令与参数（冻结；`--help` 必须可用）：

```text
lacd init [--path DIR] [--dry-run] [--force]
lacd doctor [--path DIR]
lacd status [--path DIR] [--json]
lacd verify [--path DIR] [--strict] [--evidence FILE]
lacd upgrade --check [--path DIR] [--template-dir DIR]
lacd upgrade --dry-run --to VERSION [--path DIR] [--template-dir DIR]
lacd upgrade --to VERSION [--path DIR] [--prepare-pr] [--accept-major]
      [--template-dir DIR] [--yes]
```

细化（2026-10-12 集成）：`--template-dir` 是**更新源选择器**（离线/测试），
因此 `--check` 也可以带它；`--dry-run` 同理。`--yes` 跳过应用前的人工确认
提示（默认会提示）。

`apply_migration` 幂等细化：当三方比较结果全为 noop（无 writes）时，
直接返回空 `ApplyResult`，不建备份目录、不重写 `lock.json`，
保证"升级后重跑同一版本零 diff"（2026-10-12 集成增补）。

退出码：`0` 成功；`1` 用法/一般错误；`2` 校验失败（含 `--strict` 下关键未验证）；
`3` 升级被拒绝（major 未接受、冲突、安全策略阻止）。

行为契约：
- `init`：检测已有 `lacd.yaml`（存在且非 `--force` → 报错退出 1，不覆盖）；
  `--dry-run` 只打印拟写入文件清单与 diff 摘要，不写盘；幂等（二次运行无 diff，
  退出 0 并提示 `already-initialized`）；写 `lacd.yaml`、`.lacd/lock.json`、
  `.lacd/project-rules.yaml`（如不存在则建空模板）、受管模板文件、
  `AGENTS.md` 受管 section（有则更新 section、无则追加，不删用户内容）；
  **绝不** `git add/commit/push`。
- `doctor`：检查 python>=3.11、git 可用、`gh` 可用性、写权限、
  到 `api.github.com` 的连通性（超时 10s，失败记为 warning 不直接判错）；
  以分项 PASS/WARN/FAIL 输出，FAIL 项存在则退出 1。
- `status`：人读摘要；`--json` 输出 §2.4（经 schema 校验后输出）。
- `verify`：本地维度全量检查（config/lock/drift/version-consistency）；
  远端维度仅在能成功调用 API 时判定 enforced/not_enforced，
  403/无凭证/网络失败 → unverified 并记录原因码；
  `--strict`：任一关键维度非 pass/enforced（即 fail 或 unverified 且
  `github_enforcement_required=true`）→ 退出 2；
  `--evidence FILE`：读取 JSON 证据文件，校验其 `head_sha` 与当前一致，
  不一致 → 退出 2 并报 `EVIDENCE_SHA_MISMATCH`（AT-22）。
- `upgrade --check`：查询可信源，列出当前锁定版本、可用版本、
  每个版本的 bump 分类与支持状态；源不可达/元数据非法 → 明确报错并退出 1，
  **绝不**输出 "already latest"（AT-12）。
- `upgrade --dry-run --to V`：输出变更清单（文件级：add/update/no-op/conflict）、
  风险等级、兼容性结论、人工检查点；**不修改工作树**（AT-13）。
- `upgrade --to V`：预检（版本合法、bump 分类、major 需 `--accept-major`、
  冲突预检）；备份原文件到 `.lacd/backup/<UTC timestamp>/`；三方合并；
  冲突 → 中止并恢复（或保持未动，明确报告）；成功后更新 lock.json；
  幂等（重复执行同版本 → `already-at-version`，无 diff）；
  `--prepare-pr`：走 `github/pr.py`（见 §6）；永不自动合并。
- `--template-dir`：用本地模板包代替远端 bundle（测试/离线用）。

## 4. 治理：风险分级（`governance/risk.py`）

```python
def classify_change(paths: list[str]) -> str: ...
def classify_path(path: str) -> str: ...  # "T0" | "T1" | "T2"
```

规则（按顺序，首个命中；未知/非法 → T2）：
- T2：`.github/**`；`.lacd/**`；`**/workflows/**`；文件名含 `secret|credential|token|private`；
  后缀 `.pem/.key/.p12/.pfx`；`SECURITY.md`；路径逃逸/空路径/绝对路径。
- T0：`docs/**`、`examples/**`、`README*`、`LICENSE*`、`NOTICE*`、`CHANGELOG*`
  （`SECURITY.md` 除外，已归 T2；`AGENTS.md` 除外，见下）。
- T1：`AGENTS.md`（共享文件）、`src/**`、`tests/**`、`schemas/**`、
  `pyproject.toml`、其他一切常规文件。
- `classify_change` 取所有路径的最高等级（T2 > T1 > T0）。

## 5. 升级子系统

### 5.1 更新源（`upgrade/source.py`）

```python
@dataclass
class ReleaseInfo:
    version: str            # "1.0.1"
    tag: str                # "v1.0.1"
    commit: str | None      # tag 指向的 commit（可为 None）
    published_at: str | None
    prerelease: bool
    template_bundle: dict | None  # {"url": ..., "sha256": ...} | None
    notes: str

class UpgradeSource:
    def list_releases(self) -> list[ReleaseInfo]: ...
    @staticmethod
    def from_config(config: LacdConfig, template_dir: str | None = None) -> "UpgradeSource": ...

class GitHubReleasesSource(UpgradeSource):
    def __init__(self, repo: str, api_base: str = "https://api.github.com", timeout: int = 15): ...
    # 网络失败/元数据非法 -> 抛 SourceUnavailable / SourceInvalid（调用方转明确错误，AT-12）

class FileSource(UpgradeSource):
    def __init__(self, path: str): ...   # 本地 JSON：{"releases": [ReleaseInfo-dict...]}，供测试
```

环境变量覆盖：`LACD_UPSTREAM_REPO`（默认取配置的 `upstream_repo`），
`LACD_TEMPLATE_DIR`（离线/测试模板包）。**禁止**下载后直接执行远端脚本；
模板 bundle 下载后必须校验 sha256（与 ReleaseInfo.template_bundle 一致），
不一致 → 抛 `BundleVerificationError`，fail-closed。

### 5.2 兼容性（`upgrade/compat.py`）

```python
@dataclass
class CompatPolicy:
    supported_minors: int = 2   # 保留最近 N 个 minor 为受支持（v1.0 默认语义）
    # 实际判定：同 major 下，version >= (latest_major.latest_minor - supported_minors + 1)
def check_compatibility(from_v: str, to_v: str, policy: CompatPolicy,
                        releases: list[ReleaseInfo]) -> CompatReport:
    """CompatReport{allowed: bool, bump: str, requires_major_acceptance: bool,
                     support_status: "supported"|"unsupported"|"unknown",
                     preconditions: list[str], notes: list[str]}"""
```

规则（与 `docs/specification/UPGRADE_CONTRACT.md` 一致）：
- patch：允许，常规升级 PR。
- minor：允许，需检查新增字段/能力（preconditions 列出）。
- major：`allowed=False` 除非调用方显式接受（`accept_major=True` 参数）；
  必须有迁移计划（preconditions 含 `migration-plan-required`）。
- downgrade：`allowed=False`（回退走专门的 rollback 流程，见 5.3）。
- 不受支持的旧版本 → `support_status="unsupported"`，报告但不强制。

### 5.3 迁移引擎（`upgrade/migrate.py`）

```python
@dataclass
class FileChange:
    path: str
    action: str        # "add" | "update" | "noop" | "conflict"
    risk: str          # "T0" | "T1" | "T2"
    detail: str
@dataclass
class MigrationPlan:
    from_version: str
    to_version: str
    changes: list[FileChange]
    conflicts: list[FileChange]
    human_checkpoints: list[str]
    rollback_dir: str | None

def plan_migration(project_dir, lock: LockFile, bundle: "TemplateBundle") -> MigrationPlan:
    """纯计算，不写盘。三方比较：
    managed(replace)：old=lock 记录的 sha256；cur=文件当前 sha256；new=bundle 内容。
      cur==old -> update；cur==new or old==new -> noop；缺失且 old 存在 -> conflict(missing)；
      否则 conflict。
    shared(marked-section)：抽取 AGENTS.md 中 markers 间内容做同样三方比较；
      无 markers -> 视为 add-section（非 conflict，不删用户内容）。
    project-owned：永不列入 changes（只读校验存在性）。"""

def apply_migration(project_dir, plan: MigrationPlan, bundle) -> ApplyResult:
    """先备份 (.lacd/backup/<ts>/) 再逐文件应用；任一失败 -> 回滚已应用并抛 MigrationError；
    有 conflict -> 直接拒绝应用（不写盘），抛 MigrationConflict。成功后更新 lock.json
    （template_version/to_version、managed_files 新 hash、generated_at/by）。"""

def rollback_last(project_dir) -> bool:
    """用最近一次 backup 恢复；成功返回 True。供故障回退（AT-20）。"""
```

`TemplateBundle`：`{"version": str, "files": {relpath: bytes}, "manifest": dict}`；
从目录加载（`load_bundle_from_dir`）或从 zip bytes 加载（`load_bundle_from_zip`，
先验 sha256）。

### 5.4 升级规划（`upgrade/planner.py`）

`build_upgrade_plan(project_dir, to_version, *, source, bundle, accept_major=False) -> MigrationPlan`
编排：读锁 → 查源 → 兼容性检查 → plan_migration → 附加 human_checkpoints
（风险 T2 项逐条列出；major 列迁移计划要求）。

## 6. GitHub 集成

### 6.1 远端核验（`github/remote.py`）

```python
@dataclass
class EnforcementReport:
    status: str          # "enforced" | "not_enforced" | "unverified"
    reasons: list[str]   # 如 REMOTE_PROTECTION_VERIFIED / REMOTE_PROTECTION_ABSENT /
                         #     REMOTE_PERMISSION_DENIED / REMOTE_NO_CREDENTIAL / REMOTE_NETWORK_ERROR
    details: dict        # 原始关键事实（分支保护/ruleset 摘要），不含秘密

def check_remote_enforcement(repo: str, branch: str,
                             token: str | None = None,
                             timeout: int = 15) -> EnforcementReport:
    """token 为 None 时尝试 gh CLI / GITHUB_TOKEN；仍无 -> unverified/REMOTE_NO_CREDENTIAL。
    判定 enforced 的最低标准：默认分支要求 PR（required_pull_request_reviews 或 ruleset
    pull-request 规则存在）即算 enforced=True 的必要条件；required_status_checks 存在则加分
    记录在 details。注意：本函数只做"存在性核验"，不创建/修改任何远端配置。"""
```

`compute_state` 集成：`verify` 命令在非 `--offline` 时调用本函数，结果注入
`remote` 参数。API 403 → unverified（AT-07），绝不把异常当通过。

### 6.2 升级 PR（`github/pr.py`）

```python
@dataclass
class PrReport:
    created: bool
    reused: bool
    pr_url: str | None
    branch: str
    local_patch: str | None   # 无权限时生成的 patch 路径
    steps: list[str]          # 人工步骤（无权限时）

def upgrade_branch_name(to_version: str) -> str:  # "lacd/upgrade-to-<version>"
def find_existing_upgrade_pr(repo, to_version, token) -> dict | None: ...
def prepare_upgrade_pr(project_dir, to_version, plan: MigrationPlan,
                       bundle: TemplateBundle, repo: str, token: str | None,
                       base: str = "main") -> PrReport:
    """1. 查重复 PR（有则 reused=True 返回，不新建）。
    2. 检查工作树干净；建分支（基于当前 HEAD）；**在新分支上**应用迁移
       （`apply_migration`）；提交前删除迁移备份目录（分支上 git 历史即
       安全网，提交必须只含迁移内容）；提交（message 含版本与 plan 摘要）。
    3. 有 push 权限 -> 只 push 升级分支；创建 PR（body 含版本差异/迁移项/
       风险分级/测试计划/未解决冲突/人工检查点/HEAD SHA）；无权限 -> 写
       `.lacd/upgrade-<version>.patch` + steps。
    4. 永不 merge；永不 push 到 base 分支。
    （2026-10-12 集成修订：旧签名无 bundle、要求调用方先应用迁移，
    与"提交只含迁移"的干净树检查自相矛盾；现由本函数拥有完整事务。）"""
```

## 7. Agent 协议（`agents/protocol.py`）

```python
def read_project_instructions(project_dir) -> dict:
    # {"found": bool, "path": str|None, "managed_section_present": bool, "chars": int}
def agent_checklist(project_dir, intended_paths: list[str] | None = None) -> dict:
    # {"instructions": {...}, "status": {status JSON}, "risk_tier": "T0|T1|T2|None",
    #  "rules": ["...human-readable..."], "ready": bool}
```

`init` 写入的 AGENTS.md 受管 section 标记：
`<!-- LACD:MANAGED:BEGIN section="lacd-agent-protocol" version="1.0.0" -->`
`<!-- LACD:MANAGED:END -->`。
AT-24：`agent_checklist` 必须实际调用 `core` 的状态计算（不是读静态文件）。

## 8. 模板（`src/lacd/templates/`）

- `manifest.json`：`{"template_version": "1.0.0", "files": [{"path": ..., "sha256": ...}]}`，
  构建时由脚本生成校验（测试覆盖）。
- `managed/agents-md.section.md`：AGENTS.md 受管 section 内容。
- `workflows/lacd-verify.yml`：采用者 CI 模板（跑 `pipx run` 或本地 lacd verify；
  默认用 `gh` 官方 action pin 到**完整 commit SHA**，SHA 在 manifest/release notes 中声明）。
- `project-rules.template.yaml`：`.lacd/project-rules.yaml` 初始模板（空扩展示例）。

## 9. 测试契约

- `pytest`；目录 `tests/{unit,integration,acceptance,security,e2e}`。
- `tests/acceptance/test_at_matrix.py`：AT-01..AT-25 每项一个测试函数
  `test_at_01_...`，远端依赖的用 `pytest.mark.skipif`（无凭证时标记为
  `UNVERIFIED` 而非失败——用 `pytest -v` 输出 + 验收报告如实记录）。
- `tests/e2e/`：Alpha/Beta 合成仓库脚本（`run_e2e.py` 可独立运行），全本地 git，
  用 `FileSource` + 本地 bundle，不依赖外网与真实业务仓库。
- 安全负面测试（`tests/security/`）：伪造 evidence SHA、篡改 lock、模板 bundle
  sha 不符、旧 HEAD 证据复用、未知路径提权（T2）、静默覆盖用户改动（conflict）。
- 覆盖率目标：核心模块（core/config/upgrade）行覆盖 ≥ 80%（CI 强制，
  `pytest --cov=lacd --cov-fail-under=80`，仅对核心包）。

## 10. 不可违反的铁律（实现者必读）

1. 永远不要输出"已是最新"除非源成功返回且确实无更高版本。
2. 永远不要在未验证时报告 ENFORCED。
3. 永远不要静默覆盖用户文件；冲突必须显式。
4. 永远不要自动合并 PR；`prepare-pr` 只建 PR。
5. 永远不要下载执行未经校验的远端脚本；bundle 必验 sha256。
6. 永远不要把 `latest/main/v1` 浮动引用写入锁文件的安全敏感字段。
7. 测试不许为通过而放宽断言（防测试弱化）；新增测试先在旧行为上 FAIL
   （如适用，在 PR 描述中说明证伪过程）。
8. 所有对外文档中的命令必须真实跑过（AT-25，docs smoke test 覆盖）。

---

## §9. v1.0.1 增量（2026-10-12 Freeze）

### 打包（`pyproject.toml` / `src/lacd/__init__.py`）
- wheel 必须含 `lacd/templates/**` 与 `lacd/schemas/*.json`（`package-data` 显式声明，
  `src/lacd/templates/__init__.py` 使其成为包）。
- 资源定位一律经 `importlib.resources`（`lacd.cli.templates_dir()` /
  `lacd.config.schemas_dir()` 两个唯一入口），禁止 `Path(__file__)` 硬编码。

### CLI（`cli/main.py`）
```text
lacd verify [--path DIR] [--strict] [--evidence FILE] [--local] [--remote]
```
- `--local` 与 `--remote` 互斥；`--local` 时不做任何网络调用，
  `remote_enforcement = unverified/LOCAL_ONLY`。
- `status --json` 新增 `dimensions.remote_enforcement.query: live|not_queried`；
  `status` 本身永不触发网络。

### 远端治理（`github/remote.py`）
```python
@dataclass
class EnforcementPolicy:
    policy_version: str = "1"
    require_pull_request: bool = True
    required_checks: list[str] = field(default_factory=list)
    allow_bypass: bool = False
    human_merge_required: bool = True   # solo 仓 0 approvals 时必须为真

def check_remote_enforcement(repo, branch, *, policy: EnforcementPolicy | None = None,
                             token=None, timeout=15) -> EnforcementReport:
```
- `policy=None` 时沿用 v1.0.0 语义（PR requirement 即 ENFORCED 的旧行为标记为 deprecated，
  文档注明其不充分性）。
- 有 policy 时：逐项核验；任一关键项不可读 → `unverified`；不满足 → `not_enforced`；
  全部证实 → `enforced`。`EnforcementReport.details` 必须列出每项的 `checked: true/false`
  与证据来源。

### 升级源（`upgrade/source.py`）
```python
def parse_template_bundle(release: dict) -> dict | None:
    """三种格式（闭合 JSON 围栏 / Template-Bundle-SHA256 行 / 单资产兜底），
    fail-closed：无可信 sha256 时返回 None，调用方拒绝下载。"""
```
- 旧 `_parse_template_bundle` 保留为内部兼容；新解析对未闭合围栏、缺 sha256、
  sha256 非 64 hex 一律返回 None（回归测试用真实 v1.0.0 Release body）。

### 锁文件（`config/lockfile.py`）
- `source` 段新增可选 `commit`（完整 SHA）与派生 `pin_strength: strong|tag-only`。
- v1.0.0 锁（无 commit）可读；`verify` 对 `tag-only` 明确标注，不谎称强锁定。

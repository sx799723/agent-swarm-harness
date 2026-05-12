# MonoSwarm 架构分析文档

**分析日期**: 2026-05-12  
**源码目录**: `/Users/yutanglao/.hermes/agent-swarm/`  
**核心文件**: `ceo_brain.py`、`harness.py`、`worker_pool.py`、`session_store.py`、`config.py`、`run.py`、`api_server.py`  
**源码规模**: CEO~760行 | Harness~379行 | WorkerPool~375行 | SessionStore~329行 | Config~8行 | run.py~101行 | api_server.py~153行

---

## 一、项目概述

MonoSwarm 是一个基于 Hermès Agent 的多 Worker 并行执行框架，定位为"CEO + Worker Pool"架构的任务调度系统。其核心设计思想是：

- **CEO Brain** 负责任务理解、拆解、Context Passing 和结果汇总
- **Harness** 负责任务分发、执行调度、重试和状态管理
- **Worker Pool** 负责任务的实际执行（通过 `hermes chat -q` 启动独立子进程）
- **SessionStore** 负责所有状态的 SQLite 持久化

整个系统完全基于 Python 标准库，无第三方依赖，通过 `hermes` CLI 调用大模型执行实际任务。

---

## 二、核心模块职责

### 2.1 CEO Brain（`ceo_brain.py`，~760行）

**职责**：任务理解 → 拆解子任务 → Context Passing 编排 → 结果汇总

**核心 API**：
- `decompose(task_description)` — 双轨拆解（规则/LLM）
- `execute(decomposition)` — 分 wave 执行，含 Hand Passing
- `aggregate(task_id, task_goal, worker_results)` — Markdown 报告生成
- `run_full_flow(task_description)` — 完整流程一键执行

**Skill 动态路由**：`select_skill_for_task()` 根据 worker_type + goal 内容关键词匹配最优 skill 路径。

### 2.2 Harness（`harness.py`，~379行）

**职责**：CEO Brain 与 Worker Pool 之间的调度编排层

**核心 API**：
- `create_task(title, description, goal)` — 创建任务记录
- `dispatch(task_id, workers)` — 分发 workers 到 SQLite
- `execute_all(worker_ids, parallel)` — 并发/串行执行
- `retry_failed(worker_ids)` — 失败重试
- `run(task_id, workers)` — 一站式执行（分发→执行→重试→汇总）
- `cancel_task(task_id)` — 取消任务
- `get_task_status(task_id)` — 查询状态

### 2.3 Worker Pool（`worker_pool.py`，~375行）

**职责**：Worker 生命周期管理

**核心 API**：
- `spawn(worker_id, worker_type, goal, context, ...)` — 启动独立子进程，**非阻塞**
- `get_result(worker_id)` — 非阻塞获取结果
- `wait_for_result(worker_id)` — 阻塞等待
- `kill(worker_id)` — 强制终止
- `kill_all()` — 全部终止

**执行方式**：每个 Worker 通过 `subprocess.Popen` 启动 `hermes chat -q "{goal}"` 子进程，结果通过 daemon 线程轮询获取。

### 2.4 SessionStore（`session_store.py`，~329行）

**职责**：SQLite 持久化层

**核心 API**：
- `create_task()` / `get_task()` / `update_task_status()`
- `create_worker()` / `get_worker()` / `update_worker_status()`
- `assign_worker_to_task()` / `is_all_workers_done()` / `get_task_stats()`
- `get_event_log()` / `get_pending_workers()`

### 2.5 Config（`config.py`，~8行）

**唯一内容**：`PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))`，项目根目录常量。

### 2.6 CLI（`run.py`，~101行）

入口点，支持命令：
- `python3 run.py "任务描述"` — 执行任务
- `python3 run.py status <task_id>` — 查看任务状态
- `python3 run.py log <task_id>` — 查看事件日志
- `python3 run.py tasks` — 列出所有任务
- `python3 run.py test` — 运行自测

### 2.7 API Server（`api_server.py`，~153行）

独立的 Flask REST API 服务，提供用户注册/查询功能，与 MonoSwarm 主框架无直接耦合，是一个独立的示例项目。

---

## 三、CEO Brain 拆解逻辑

### 3.1 双轨拆解策略

```python
# 判断是否需要 LLM
has_complex_structure = any(sig in task for sig in ["和", "然后", "同时", "并且", "以及", "加", "或者"])
has_clear_type = any(kw in task.lower() for kw in ["ppt", "视频", "调研", "测试", "文档", "写代码", "开发", "设计", "ui"])

use_llm = has_complex_structure or not has_clear_type
```

- **简单任务**（有明确类型关键词，无复杂结构）→ `_rule_based_decompose()`，规则匹配，快速
- **复杂任务**（多意图、无明确类型）→ `_llm_decompose()`，通过 `hermes chat -q` 调用 LLM 理解并返回 JSON 拆解结果

### 3.2 规则拆解逻辑

关键词驱动型分类：

| 维度 | 触发关键词 | Worker 类型 |
|------|-----------|------------|
| 代码开发 | "写代码/开发/实现/脚本" 或 "python/javascript/网页/api/爬虫" | `code_worker` |
| 测试验证 | "测试/验证/评审/检查/评估/审查" | `qa_worker` |
| 调研搜索 | "调研/调查/研究/搜索/搜集/市场" | `research_worker` |
| PPT演示 | "ppt/演示/幻灯片/presentation" | `ppt_worker` |
| UI设计 | "设计/ui/海报/icon/logo/视觉" | `ui_worker` |
| 视频制作 | "视频/剪辑/movie" | `video_worker` |
| 文档报告 | "文档/报告/表格/excel/csv/季度/年度" 或 "整理"+"文档/报告" | `doc_worker` |
| 无匹配 | — | `generic_worker` |

**优先级原则**：具体类型（ppt/video/文档）优先于通用动词（做/写），避免误触发。

### 3.3 LLM 拆解格式

```json
{
  "task_title": "任务标题（50字内）",
  "task_description": "完整任务描述",
  "task_goal": "CEO层总体目标",
  "subtasks": [
    {
      "title": "子任务标题",
      "goal": "给 Worker 的具体目标描述",
      "worker_type": "code_worker/ppt_worker/doc_worker/...",
      "context": {"aspect": "development/presentation/..."}
    }
  ]
}
```

### 3.4 Skill 动态路由

每个 worker_type 维护一个 `(关键词列表, skill路径)` 映射表，精确匹配优先，未匹配则使用默认 skill：

```python
WORKER_DEFAULT_SKILLS = {
    "code_worker": "software-development/skill-creator",
    "ppt_worker": "productivity/ppt-workflow",
    "video_worker": "media/youtube-content",
    "ui_worker": "creative/baoyu-cover-image",
    "qa_worker": "software-development/test-driven-development",
    "doc_worker": "productivity/spreadsheet",
    "research_worker": "find-skills-skill/find-skills",
    "generic_worker": "find-skills-skill/find-skills",
}
```

生成的 Worker goal 中会包含：
```
【Skills 可用】: software-development/skill-creator
【Skills 调用指令】
请使用 hermes chat -s software-development/skill-creator 来加载对应技能后执行任务。
```

---

## 四、Worker Pool 调度机制

### 4.1 Worker 类型 → Skill 映射

```python
WORKER_TYPE_SKILLS = {
    "code_worker":     "software-development/skill-creator",
    "ppt_worker":      "productivity/ppt-workflow",
    "video_worker":    "media/youtube-content",
    "ui_worker":       "creative/baoyu-comic",
    "qa_worker":       "software-development/test-driven-development",
    "doc_worker":      "productivity/spreadsheet",
    "research_worker": "research/arxiv",
    "generic_worker":  "",
}
```

### 4.2 spawn 非阻塞执行流程

```python
cmd = f"hermes chat {skill_flag} -q {json.dumps(full_goal)} --quiet"
proc = subprocess.Popen(cmd, shell=True, stdout=PIPE, stderr=PIPE, cwd=PROJECT_ROOT)
# 立即返回，在独立 daemon 线程中监控结果
thread = threading.Thread(target=self._monitor, args=(worker_id, timeout), daemon=True)
thread.start()
return worker_id
```

### 4.3 Daemon 监控线程（`_monitor`）

- 调用 `proc.communicate(timeout=timeout)` 等待子进程
- 解析 stdout/stderr：提取分级日志 `[INFO/WARN/ERROR]` 和进度条 `[PROGRESS]`
- 根据 returncode 设置 `WorkerResult(status="completed"/"failed"/"timeout")`
- 持有 `callback` 时调用回调

### 4.4 日志解析

```python
LOG_PATTERN = re.compile(r"^\[(INFO|WARN|ERROR|DEBUG)\]\s*(.*)$")
PROGRESS_PATTERN = re.compile(r"^\[PROGRESS\]\s*(\d+(?:\.\d+)?)\s*(?:[-:]\s*(.*))?$")
```

### 4.5 并发控制

- Harness 层：`ThreadPoolExecutor(max_workers=max_concurrent)` 管理并发
- 默认 `max_concurrent=7`，`max_retries=3`，`timeout=7200`（2小时，硬编码）
- WorkerPool 本身无并发上限，依赖 Harness 的 `execute_all()` 调度

### 4.6 Wave 分层执行（拓扑排序）

依赖规则：
```python
dependency_rules = {
    "doc_worker":  ["code_worker"],     # 文档依赖代码
    "qa_worker":   ["code_worker"],     # 测试依赖代码
    "ppt_worker":  ["doc_worker", "code_worker"],  # PPT 依赖文档+代码
}
```

`_build_execution_waves()` 算法：
1. 遍历所有 worker，找所有依赖已完成的 worker 加入当前 wave
2. 将当前 wave 标记为完成，重复直到所有 worker 被分配
3. 同 wave 内无依赖关系，强制并行；不同 wave 顺序执行

### 4.7 Hand Passing 机制

在 wave 执行切换时，`_inject_upstream_results()` 把上游 Worker 的产出注入下游 Worker 的 goal：

```python
injection_text = (
    "【Hand Passing：上游Worker产出】\n"
    f"Worker [{up_id}]（{up['worker_type']}）产出：\n"
    f"  产出目录: {up_output_dir}\n"
    f"  文件列表:\n{files_str}\n"
)
w["goal"] = injection_text + w["goal"]
```

通过 `glob.glob(os.path.join(up_output_dir, "*"))` 读取上游产出文件列表。

---

## 五、SessionStore 持久化设计

### 5.1 数据库 Schema

SQLite 单文件：`~/.hermes/agent-swarm/swarm.db`

```sql
-- tasks 表：任务主表
CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    parent_id TEXT,
    title TEXT NOT NULL,
    description TEXT,
    goal TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    result TEXT,
    error TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    completed_at TEXT
)

-- workers 表：Worker 执行表
CREATE TABLE workers (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    worker_type TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    goal TEXT NOT NULL,
    context TEXT,           -- JSON 存储 context dict
    result TEXT,
    error TEXT,
    retry_count INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 3,
    session_id TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    started_at TEXT,
    completed_at TEXT,
    FOREIGN KEY (task_id) REFERENCES tasks(id)
)

-- event_log 表：审计日志（不可变，仅追加）
CREATE TABLE event_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    event TEXT NOT NULL,
    detail TEXT,
    created_at TEXT DEFAULT (datetime('now'))
)

-- ceo_assignments 表：Task ↔ Worker 多对多关系
CREATE TABLE ceo_assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    worker_id TEXT NOT NULL,
    assigned_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (task_id) REFERENCES tasks(id),
    FOREIGN KEY (worker_id) REFERENCES workers(id)
)
```

**索引**：
```sql
CREATE INDEX idx_workers_task ON workers(task_id)
CREATE INDEX idx_workers_status ON workers(status)
CREATE INDEX idx_events_entity ON event_log(entity_type, entity_id)
```

### 5.2 连接模式

每次操作独立 `sqlite3.connect()`，执行完立即 `close()`，无连接池。

### 5.3 状态机

**Task 状态流**：
```
pending → running → completed
                    → partial（部分 Worker 失败）
                    → failed（全部失败）
                    → cancelled（手动取消）
```

**Worker 状态流**：
```
pending → running → completed
                    → failed（可重试）
                    → cancelled（手动终止）
                    → timeout（2小时）
```

### 5.4 持久化内容总结

| 内容 | 存储位置 | 写入时机 | 覆盖策略 |
|------|---------|---------|---------|
| Task 元信息 | `tasks` 表 | `create_task()` 时创建，`update_task_status()` 时更新 | 完成后覆盖 result/error |
| Worker 元信息 | `workers` 表 | `create_worker()` 时创建，`update_worker_status()` 时更新 | 完成后覆盖 result/error |
| 状态变更事件 | `event_log` 表 | `_log()` 每次状态变更时 | 仅追加，不可变 |
| Task-Worker 关系 | `ceo_assignments` 表 | `assign_worker_to_task()` | 仅追加 |

### 5.5 已知局限

- `result` 字段直接覆盖，无版本历史
- `event_log` 仅记录状态变更，无 stdout/stderr
- `parent_id` 字段存在但 Harness 层未使用
- `session_id` 实际复用 `worker_id`，无独立会话管理
- 无迁移脚本，Schema 变更需手动处理

---

## 六、数据流描述

### 6.1 完整执行流程

```
用户输入 "任务描述"
  │
  ▼
CEOBrain.run_full_flow()
  │
  ├─ 1. decompose(task_description)
  │       │
  │       ├─ 判断 use_llm = has_complex_structure or not has_clear_type
  │       │
  │       ├─ use_llm=False → _rule_based_decompose() [关键词匹配]
  │       │       返回 dict: {task_title, task_description, task_goal, subtasks[]}
  │       │
  │       └─ use_llm=True → _llm_decompose() [hermes chat -q 调用 LLM]
  │               返回 JSON 格式的 dict（含 subtasks）
  │
  ├─ 2. execute(decomposition)
  │       │
  │       ├─ harness.create_task() → 写入 tasks 表
  │       │
  │       ├─ 构建 Context Passing 信息（dependency_rules 拓扑排序）
  │       │       workspace/{task_id}/{worker_id}/ 每个 Worker 独立目录
  │       │
  │       ├─ _build_execution_waves() → 分成多个 wave
  │       │
  │       ├─ for each wave:
  │       │       │
  │       │       ├─ _inject_upstream_results() → Hand Passing 注入上游产出
  │       │       │
  │       │       └─ harness.run(task_id, wave_workers)
  │       │               │
  │       │               ├─ dispatch() → create_worker() + assign_worker_to_task() → 写入 workers/ceo_assignments 表
  │       │               │
  │       │               ├─ execute_all(worker_ids, parallel=True)
  │       │               │       │
  │       │               │       ├─ 批量 spawn() 所有 worker（异步，不等待）
  │       │               │       │       WorkerPool.spawn() → subprocess.Popen("hermes chat -q ...")
  │       │               │       │       daemon 线程监控 proc.communicate()
  │       │               │       │
  │       │               │       └─ 轮询 get_result() 直到全部完成
  │       │               │
  │       │               ├─ retry_failed() → 失败 worker 重试（最多 max_retries 次）
  │       │               │
  │       │               └─ aggregate_results() → 汇总 Markdown 报告
  │       │
  │       └─ all_results 汇总
  │
  └─ 3. aggregate(task_id, task_goal, worker_results)
          生成 Markdown 最终报告
          update_task_status(task_id, "completed", result=report)
```

### 6.2 Worker 执行时的数据流

```
WorkerPool.spawn(worker_id, worker_type, goal, context)
  │
  ├─ 构建命令: hermes chat -s {skill} -q "{goal}\n\n[额外上下文]\n{context_json}" --quiet
  │
  ├─ subprocess.Popen() → 启动 hermes chat 子进程
  │
  ├─ daemon 线程 _monitor() 等待 proc.communicate()
  │
  ├─ 子进程 stdout → parse_worker_output() 提取 logs/progress/final_output
  │
  └─ 完成后:
          WorkerResult(status="completed"/"failed"/"timeout", result, logs, progress)
          → Harness.update_worker_status()
          → SessionStore workers 表更新
```

### 6.3 Workspace 目录结构

```
~/.hermes/agent-swarm/
  ├─ swarm.db                    # SQLite 数据库
  ├─ workspace/
  │   └─ {task_id}/
  │       ├─ {worker_id_1}/     # code_worker 产出目录
  │       │     └─ (产出文件)
  │       ├─ {worker_id_2}/      # doc_worker 产出目录
  │       │     └─ (产出文件)
  │       └─ {worker_id_3}/      # ppt_worker 产出目录
  │             └─ (产出文件)
  └─ (其他项目文件)
```

Context Passing 通过 `input_from` 字段告知下游 Worker 上游产出目录位置，Hand Passing 通过 `glob.glob()` 读取文件列表注入到下游 goal。

---

## 七、版本能力边界

### 7.1 已实现能力

**任务拆解**：
- 双轨拆解（规则/LLM）
- 7种 Worker 类型分类（code/ppt/doc/qa/ui/video/research + generic）
- Skill 动态路由（worker_type + goal 关键词 → skill 路径）
- Context Passing（workspace 目录 + 上游产出注入）
- Wave 分层执行（拓扑排序依赖图）

**执行调度**：
- 并发执行（ThreadPoolExecutor + 异步 spawn）
- 失败自动重试（最多 max_retries 次）
- 2小时超时机制
- 手动取消任务
- Worker 强制终止（kill/kill_all）

**持久化**：
- SQLite 全量持久化（Task/Worker/EventLog）
- 任务状态追踪
- 事件审计日志
- Task-Worker 关系映射

**CLI/观测**：
- status/log/tasks 命令
- Worker 粒度状态查看
- Markdown 格式执行报告

### 7.2 能力边界（未实现/不完善）

**拆解层**：
- 纯关键词匹配，无真正语义理解（LLM 拆解为备用方案且调用超时回退到规则）
- 拆解粒度不可控（any() 匹配导致一个任务可能同时触发所有类型）
- 无依赖自动推断，完全依赖 `dependency_rules` 硬编码

**执行层**：
- `spawn()` 是非阻塞的，但底层 `proc.communicate()` 是同步等待，并非真正异步 IO
- 无差异化重试策略（指数退避、错误分类重试）
- 并发上限 `max_concurrent=7` 硬编码，无动态调整
- 无 Worker 间数据共享（仅通过文件传递）
- 无任务优先级队列（priority 字段存在但 Harness 层未使用）

**持久化层**：
- 无版本历史（result 直接覆盖）
- 无 session 独立管理（session_id 复用 worker_id）
- `parent_id` 未启用，无任务层级树
- 无迁移脚本，Schema 变更困难
- 连接模式为"每次操作独立连接"，高并发下可能有锁竞争

**可观测性**：
- 无真正监控/告警
- 无结构化指标（Prometheus 等）
- 无 Web Dashboard
- 日志仅输出到 stdout，无日志文件

**测试**：
- 存在 `tests/` 目录，但无有效测试代码（`test_session_store.py` 等为空）
- 完全依赖手动 `python3 run.py test`

**API Server**：
- `api_server.py` 是独立的 Flask 用户注册服务，与 MonoSwarm 框架无集成，不是 Swarm 的一部分

### 7.3 适用场景

✅ **适合**：
- 简单任务快速拆解（关键词明确、无复杂依赖）
- 离线任务批处理（不需要实时监控）
- 小规模并发（≤7 个 Worker）

❌ **不适合**：
- 复杂语义任务（需要强 LLM 拆解能力）
- 生产级高可用服务（无重试策略、无监控）
- 需要任务层级/父子依赖的场景
- 高并发任务（SQLite 单文件锁竞争）

---

*文档生成时间: 2026-05-12*  
*分析依据: 逐行阅读源码 — `ceo_brain.py`(760行), `harness.py`(379行), `worker_pool.py`(375行), `session_store.py`(329行), `run.py`(101行), `config.py`(8行), `api_server.py`(153行)*

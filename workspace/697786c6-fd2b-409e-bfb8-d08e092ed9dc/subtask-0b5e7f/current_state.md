# MonoSwarm 现状调研与架构分析

**分析日期**: 2026-05-12  
**源码目录**: `/Users/yutanglao/.hermes/agent-swarm/package/src/`

---

## 一、架构设计

### 1.1 模块职责划分

| 模块 | 文件 | 职责 |
|------|------|------|
| CEO Brain | `ceo_brain.py` | 任务理解 → 拆解子任务 → 结果汇总 |
| Harness | `harness.py` | 调度层核心：分发/执行/重试/汇总 |
| WorkerPool | `worker_pool.py` | Worker 生命周期管理：启动/监控/终止 |
| SessionStore | `session_store.py` | SQLite 持久化：任务/Worker/事件日志 |
| CLI | `run.py` | 用户入口：status/log/tasks/test/exec |

### 1.2 调用链路

```
用户输入 (CLI/run.py)
    │
    ▼
CEOBrain.run_full_flow()
    │
    ├─► decompose()          # 任务拆解（基于关键词规则）
    │       │
    │       └─► WORKER_TYPES 定义 7 种 worker 类型
    │
    ├─► execute()             # 执行
    │       │
    │       └─► Harness.run()
    │               │
    │               ├─► dispatch()        # 创建 Worker 记录，写入 SQLite
    │               ├─► execute_all()     # ThreadPoolExecutor 并发/顺序执行
    │               │       │
    │               │       └─► execute_worker() → WorkerPool.spawn()
    │               │               │
    │               │               └─► subprocess.Popen("hermes chat -q ...")
    │               │
    │               └─► retry_failed()  # 自动重试（可选）
    │
    └─► aggregate()           # 结果汇总成 Markdown 报告
```

### 1.3 数据流

```
SQLite (swarm.db)
  ├─ tasks        # 任务表
  ├─ workers     # Worker 表（含 status/result/error/retry_count）
  ├─ event_log   # 事件日志（审计用）
  └─ ceo_assignments  # Task ↔ Worker 分配关系
```

**状态机**:
- Task: `pending` → `running` → `completed` / `partial` / `failed` / `cancelled`
- Worker: `pending` → `running` → `completed` / `failed` / `cancelled`

---

## 二、核心能力分析

### 2.1 任务拆解

**当前实现**: `_rule_based_decompose()` — 纯关键词匹配

```python
# 关键词列表（部分）
["写", "开发", "代码", "api", "前端"]  → code_worker
["ppt", "演示", "幻灯片"]              → ppt_worker
["视频", "剪辑"]                       → video_worker
["测试", "test", "验证"]               → qa_worker
```

**局限**:
- 无语义理解，纯字符串匹配
- 一个任务只能打一个 worker 类型标签（实际用 `any()` 遍历所有关键词，同一任务可产出多个 subtask）
- 代码中明确标注 `TODO: 后续替换为LLM智能拆解`

**Worker 类型映射**:

| Worker Type | Skill |
|-------------|-------|
| code_worker | `software-development/skill-creator` |
| ppt_worker | `productivity/ppt-workflow` |
| video_worker | `media/youtube-content` |
| ui_worker | `creative/baoyu-comic` |
| qa_worker | `software-development/test-driven-development` |
| doc_worker | `productivity/spreadsheet` |
| generic_worker | 无 |

### 2.2 并发调度

**实现**: `concurrent.futures.ThreadPoolExecutor`

```python
def __init__(self, max_concurrent: int = 3, max_retries: int = 3):
    self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_concurrent)
```

- 默认最大并发: 3（可配置）
- 并行/顺序可通过 `parallel: bool` 切换
- 使用 `as_completed()` 收集结果

**Worker 执行方式**: `subprocess.Popen` 启动独立进程

```python
cmd = f"hermes chat {skill_flag} -q {json.dumps(full_goal)} --quiet"
proc = subprocess.Popen(cmd, shell=True, stdout=PIPE, stderr=PIPE, cwd=PROJECT_ROOT)
stdout, stderr = proc.communicate(timeout=7200)  # 2小时超时
```

**局限**:
- 同步阻塞：父进程等待所有 Worker 完成
- 无真正的并行感知：Worker 间无数据共享/结果传递机制
- 2小时超时硬编码

### 2.3 重试机制

```python
def retry_failed(self, worker_ids: list[str]) -> dict[str, WorkerResult]:
    for wid in worker_ids:
        worker = get_worker(wid)
        if worker["retry_count"] < worker.get("max_retries", self.max_retries):
            increment_worker_retry(wid)  # 重试计数 +1，状态重置为 pending
            to_retry.append(wid)
```

- 默认最大重试: 3 次
- 重试时计数 +1，状态从 `failed` 改回 `pending`，重新执行
- 超过上限则跳过，标记为 `failed`

**局限**:
- 重试逻辑在 Harness 层，未感知具体错误类型（网络超时？代码错误？）
- 无指数退避（exponential backoff）
- 重试后结果直接覆盖，无历史版本

### 2.4 持久化

**数据库**: SQLite，位于 `~/.hermes/agent-swarm/swarm.db`

```sql
CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    parent_id TEXT,
    title TEXT NOT NULL,
    description TEXT,
    goal TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    result TEXT,
    error TEXT,
    created_at TEXT,
    updated_at TEXT,
    completed_at TEXT
)

CREATE TABLE workers (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    worker_type TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    goal TEXT NOT NULL,
    context TEXT,
    result TEXT,
    error TEXT,
    retry_count INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 3,
    session_id TEXT,
    created_at TEXT,
    started_at TEXT,
    completed_at TEXT
)

CREATE TABLE event_log (...)
CREATE INDEX idx_workers_task ON workers(task_id)
CREATE INDEX idx_workers_status ON workers(status)
```

**局限**:
- 无任务层级（parent_id 字段存在但 Harness 未使用）
- 无 Worker 输出历史（result 字段直接覆盖）
- event_log 仅记录状态变更，无 stdout/stderr

### 2.5 会话管理

- Worker 结果通过 `session_id`（实际复用 `worker_id`）关联
- 无真正的跨 Worker 会话共享
- CEO 汇总阶段只拿到文本 `result`，无结构化数据

---

## 三、运营视角

### 3.1 部署方式

```
package/
├── src/
│   ├── ceo_brain.py
│   ├── harness.py
│   ├── worker_pool.py
│   ├── session_store.py
│   ├── run.py
│   └── config.py
└── run.py test     # 自测入口
```

**启动方式**:
```bash
python3 run.py "任务描述"          # 执行任务
python3 run.py status <task_id>   # 查看状态
python3 run.py log <task_id>      # 查看日志
python3 run.py tasks               # 列出所有任务
python3 run.py test               # 自测
```

**依赖**: 仅 Python 标准库 + `hermes` CLI（外部依赖）

### 3.2 依赖管理

```python
# 无 requirements.txt
# 依赖：
#   - concurrent.futures (stdlib)
#   - sqlite3 (stdlib)
#   - hermes chat (外部 CLI)
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
```

### 3.3 测试覆盖

自测入口 (`python3 run.py test`):
```python
def cmd_test():
    ceo = CEOBrain()
    result = ceo.run_full_flow("写一个简单的Python计算器程序，支持加减乘除", parallel=True)
```

- 无单元测试
- 无集成测试
- 无 Mock 框架
- 自测依赖真实 Hermes API

---

## 四、关键缺陷与风险

| # | 问题 | 影响 | 优先级 |
|---|------|------|--------|
| 1 | 任务拆解为纯规则匹配，无法处理复杂语义 | 拆解质量差，子任务划分不合理 | 高 |
| 2 | Worker 间无数据共享/结果传递机制 | 并行执行价值受限 | 高 |
| 3 | subprocess 同步等待，无真正的异步并发 | 父进程阻塞，扩展性差 | 高 |
| 4 | 2小时超时硬编码，无法动态调整 | 长任务强制中断 | 中 |
| 5 | 重试无差异化策略（指数退避/错误分类） | 无效重试浪费资源 | 中 |
| 6 | 无真正的会话管理（session_id 复用 worker_id） | 结果追溯困难 | 中 |
| 7 | 结果仅文本汇总，无结构化数据交换 | 下游处理受限 | 低 |
| 8 | 无单元测试，完全依赖手动自测 | 迭代风险高 | 高 |
| 9 | CEOBrain.execute() 中 dispatch + execute_all 紧耦合 | 无法单独调度/恢复任务 | 中 |
| 10 | 无监控/告警机制 | 故障被动发现 | 中 |

---

## 五、总结

MonoSwarm 是一个 **轻量级任务拆解 + 并发调度** 框架，核心逻辑清晰（CEO → Harness → WorkerPool → SQLite），但工程化程度较低：

- **已完成**: 任务拆解框架、并发调度（ThreadPoolExecutor）、SQLite 持久化、Worker 生命周期管理、重试机制
- **未完成/待改进**: LLM 智能拆解、异步非阻塞执行、测试覆盖、监控告警、错误差异化处理
- **设计取舍**: 选择了简单实现（纯 Python stdlib + subprocess），以可维护性换取了功能深度

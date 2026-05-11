# MonoSwarm 架构分析文档

**分析日期**: 2026-05-12
**源码目录**: `/Users/yutanglao/.hermes/agent-swarm/`
**核心文件**: `ceo_brain.py`、`harness.py`、`worker_pool.py`、`session_store.py`、`config.py`、`run.py`

---

## 一、模块职责划分

| 模块 | 文件 | 职责 | 代码行数 |
|------|------|------|---------|
| CEO Brain | `ceo_brain.py` | 任务理解 → 拆解子任务 → 结果汇总 → Context Passing | ~760 |
| Harness | `harness.py` | 调度层核心：分发/执行/重试/汇总 | ~375 |
| WorkerPool | `worker_pool.py` | Worker 生命周期管理：启动/监控/终止 | ~307 |
| SessionStore | `session_store.py` | SQLite 持久化：任务/Worker/事件日志 | ~329 |
| Config | `config.py` | 项目根目录常量 | ~8 |
| CLI | `run.py` | 用户入口：status/log/tasks/test/exec | ~101 |

---

## 二、调用链路

```
用户: python3 run.py "任务描述"
  │
  ▼
CEOBrain.run_full_flow()
  │
  ├─► decompose()              # 任务拆解
  │       │
  │       ├─► _rule_based_decompose()   # 关键词规则匹配
  │       └─► _llm_decompose()          # LLM 智能拆解（备用）
  │
  ├─► execute()              # 分 wave 执行（Hand Passing）
  │       │
  │       ├─► _build_execution_waves()  # 分析依赖，建立拓扑排序层级
  │       │
  │       └─► Harness.run()
  │               │
  │               ├─► dispatch()        # 创建 Worker 记录，写入 SQLite
  │               ├─► execute_all()    # ThreadPoolExecutor 并发执行
  │               │       │
  │               │       └─► WorkerPool.spawn() → subprocess.Popen("hermes chat -q ...")
  │               │
  │               ├─► retry_failed()   # 失败自动重试
  │               └─► aggregate_results()  # 汇总结果
  │
  └─► aggregate()             # Markdown 最终报告
```

---

## 三、数据流设计

### 3.1 数据库 schema

SQLite 持久化于 `~/.hermes/agent-swarm/swarm.db`，共 4 张表：

```
swarm.db
  ├─ tasks              # 任务主表
  │     id, parent_id, title, description, goal,
  │     status, result, error,
  │     created_at, updated_at, completed_at
  │
  ├─ workers           # Worker 执行表
  │     id, task_id, worker_type, status, goal, context,
  │     result, error, retry_count, max_retries, session_id,
  │     created_at, started_at, completed_at
  │
  ├─ event_log         # 审计日志（只记录状态变更）
  │     id, entity_type, entity_id, event, detail, created_at
  │
  └─ ceo_assignments  # Task ↔ Worker 多对多关系
        id, task_id, worker_id, assigned_at
```

**索引**:
```sql
CREATE INDEX idx_workers_task ON workers(task_id)
CREATE INDEX idx_workers_status ON workers(status)
CREATE INDEX idx_events_entity ON event_log(entity_type, entity_id)
```

### 3.2 状态机

```
Task 状态:
  pending → running → completed
                       → partial（部分失败）
                       → failed（全部失败）
                       → cancelled（手动取消）

Worker 状态:
  pending → running → completed
                    → failed（可重试）
                    → cancelled（手动终止）
                    → timeout（2小时超时）
```

### 3.3 Context Passing 机制

CEO 在拆解任务时，会为每个 Worker 构造 context，注入上下游信息：

```python
context = {
    "task_id": task_id,
    "workspace": workspace,
    "output_dir": output_dir,           # 本 Worker 产出目录
    "input_from": upstream_workers,     # 上游 Worker 列表
    "worker_type": worker_type,
    "subtask_title": title,
}
```

Hand Passing 在 wave 执行时，把上游 Worker 的产出文件路径和结果摘要注入到下游 Worker 的 goal 中：

```python
# 上游产出 → 注入到下游 goal
injection_text = (
    "【Hand Passing：上游Worker产出】\n"
    f"Worker [{up_id}]（{up['worker_type']}）产出：\n"
    f"  产出目录: {up_output_dir}\n"
    f"  文件列表:\n{files_str}\n"
)
w["goal"] = injection_text + w["goal"]
```

---

## 四、并发模型

### 4.1 执行架构

```
Harness.execute_all(parallel=True)
  │
  ├─► 第一阶段：所有 worker 同时 spawn（异步，不等待）
  │       WorkerPool.spawn() → subprocess.Popen("hermes chat -q ...")
  │       daemon thread 监控结果
  │
  └─► 第二阶段：轮询所有 worker 结果
          while pending:
              result = WorkerPool.get_result(wid)
              if result: pending.remove(wid)
              time.sleep(1.0)
```

### 4.2 Wave 分层执行

基于 worker 类型依赖规则的拓扑排序：

```python
dependency_rules = {
    "doc_worker":  ["code_worker"],     # 文档依赖代码产出
    "qa_worker":   ["code_worker"],     # 测试依赖代码产出
    "ppt_worker":  ["doc_worker", "code_worker"],  # PPT 依赖文档/代码
}
```

同一 wave 内无依赖关系，强制并行；不同 wave 必须顺序执行。

### 4.3 并发参数

```python
max_concurrent = 7          # 最大并发 Worker 数
max_retries = 3             # 最大重试次数
worker_timeout = 7200       # 2小时超时（硬编码）
```

### 4.4 Worker 执行命令

```python
cmd = f"hermes chat {skill_flag} -q {json.dumps(full_goal)} --quiet"
proc = subprocess.Popen(
    cmd,
    shell=True,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    cwd=PROJECT_ROOT,
)
```

每个 Worker 通过 `hermes chat -q` 启动独立 Hermès Agent 子进程执行实际任务。

---

## 五、持久化策略

### 5.1 当前实现

- **数据库**: SQLite（单文件，无服务器依赖）
- **路径**: `~/.hermes/agent-swarm/swarm.db`
- **初始化**: `session_store.py` 末尾自动 `init_db()`
- **连接模式**: 每次操作独立连接，操作完立即关闭

### 5.2 持久化内容

| 内容 | 存储位置 | 覆盖策略 |
|------|---------|---------|
| Task 元信息 | `tasks` 表 | 完成后更新 result/error |
| Worker 元信息 | `workers` 表 | 完成后更新 result/error/retry_count |
| 状态变更事件 | `event_log` 表 | 仅追加，不可变 |
| Task-Worker 关系 | `ceo_assignments` 表 | 仅追加 |

### 5.3 局限

- `result` 字段直接覆盖，无版本历史
- `event_log` 仅记录状态变更，无 stdout/stderr
- `parent_id` 字段存在但 Harness 层未使用
- 无独立的 Session 表，`session_id` 实际复用 `worker_id`

---

## 六、CEO 拆解规则现状

### 6.1 双轨拆解策略

```python
# 规则判断：是否需要 LLM
has_complex_structure = 任务含"和/然后/同时/并且"
has_clear_type = 任务含"ppt/视频/调研/测试/文档/写代码/开发/设计/ui"

use_llm = has_complex_structure or not has_clear_type
```

- **简单任务**: 关键词规则匹配（快速）
- **复杂任务**: LLM 智能理解（通过 `hermes chat -q` 调用）

### 6.2 关键词 → Worker 类型映射

```python
needs_code      = 含"写代码/开发/实现/脚本"或"python/javascript/网页/api/爬虫"
needs_qa        = 含"测试/验证/评审/检查/评估/审查"
needs_research  = 含"调研/调查/研究/搜索/搜集/市场"
needs_ppt       = 含"ppt/演示/幻灯片/presentation"
needs_ui        = 含"设计/ui/海报/icon/logo/视觉"
needs_video     = 含"视频/剪辑/movie"
needs_doc       = 含"文档/报告/表格/excel/csv/季度/年度"或"整理"+"文档/报告/表格"
```

### 6.3 Skill 动态路由

```python
SKILL_ROUTING = {
    "code_worker": [
        (["写代码", "开发", "实现", "构建"], "software-development/skill-creator"),
        (["调试", "debug", "错误", "修复bug"], "software-development/systematic-debugging"),
        ...
    ],
    "research_worker": [
        (["arxiv", "论文", "学术"], "research/arxiv"),
        (["深度调研", "市场分析"], "sn-deep-research"),
        ...
    ],
}
```

Worker 执行时，`_make_worker_goal()` 会将匹配的 skill 通过 `hermes chat -s` 注入到 Worker 的 goal 指令中。

### 6.4 拆解粒度

- 一个任务可产出多个不同类型的 subtask（用 `any()` 遍历关键词）
- 每个 subtask 有独立 `id`、`title`、`goal`、`worker_type`
- 上游/下游 Worker 通过 Context Passing 传递产出文件位置

---

## 七、现有分析摘要

基于 `MONOSWARM_TEST_REPORT.md`（实际文件位于 workspace 历史记录中），核心发现：

### 7.1 能力评估

| 能力 | 评分 | 说明 |
|------|------|------|
| 任务拆解 | ⚠️ 初级 | 纯关键词匹配，无语义理解 |
| 并发调度 | ⚠️ 表面并行 | subprocess 同步等待，父进程阻塞 |
| 重试机制 | ⚠️ 基础可用 | 无指数退避，无错误分类 |
| 持久化 | ✅ 核心完整 | SQLite CRUD 完整，但无版本历史 |
| 会话管理 | ❌ 缺失 | session_id 复用 worker_id，无跨 Worker 共享 |

### 7.2 关键缺陷（按优先级）

**P0（阻断）**:
1. 任务拆解为纯规则，无法处理复杂语义
2. subprocess 同步等待，无真正异步并发
3. 无单元测试，完全依赖手动自测

**P1（影响效率）**:
4. Worker 间无数据共享机制
5. 重试无差异化策略
6. dispatch 与 execute_all 紧耦合，无法单独恢复任务
7. 无监控/告警机制
8. 2小时超时硬编码

**P2（体验优化）**:
9. 无真正会话管理
10. 结果仅文本汇总，无结构化数据
11. parent_id 未启用，无任务层级

### 7.3 进化路线

```
Phase 1 (2026 Q2): 基础可用
  → LLM 智能拆解 + 单元测试 + dispatch/execute 分离

Phase 2 (2026 Q3): 生产可用
  → 异步执行引擎 + 会话管理重构 + 差异化重试

Phase 3 (2026 Q4): 规模化
  → Worker 数据共享 + 任务层级树 + 监控仪表盘

Phase 4 (2027 Q1): 智能化
  → LLM 自适应拆解 + 预测式扩容 + 多租户隔离
```

---

## 八、架构特点总结

1. **极简依赖**: 仅 Python stdlib + 外部 Hermès CLI，无第三方包
2. **模块清晰**: CEO → Harness → WorkerPool → SQLite，职责边界明确
3. **Context Passing**: 通过 workspace 目录和 context dict 实现 Worker 间数据传递
4. **Wave 级并行**: 依赖拓扑排序确保上下游顺序，支持同层并行
5. **Skill 动态路由**: 根据 worker_type + goal 内容自动选择最优 skill

**核心局限**: 拆解规则原始、并发为伪异步、持久化无版本历史、测试覆盖为零。

---

*文档生成时间: 2026-05-12*
*分析依据: `ceo_brain.py` (760行), `harness.py` (375行), `worker_pool.py` (307行), `session_store.py` (329行), `run.py` (101行), `config.py` (8行)*

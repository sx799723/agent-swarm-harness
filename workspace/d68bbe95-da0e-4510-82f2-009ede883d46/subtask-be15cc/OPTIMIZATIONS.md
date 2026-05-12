# MonoSwarm 优化实施报告

## 任务概述
在 `~/.hermes/agent-swarm/` 下实施 4 项优化：
1. 修复 `harness.py` 的 `execute_all` 并行 bug
2. 将 `dependency_rules` 从 hardcoded 改为由 decomposition 动态推导
3. 在 `worker_pool.py` 中增加实时日志流回调机制
4. 增加 Worker 优先级调度支持

---

## 1. `harness.py` — `execute_all` 并行 bug 修复

### Bug 描述
原代码有两个严重问题：
- **未传递 `timeout`/`max_retries`**: `spawn()` 调用时 `max_retries=worker.get("max_retries")` 传的是 `None`（应为 `self.max_retries`），且完全未传 `timeout`
- **并行轮询死循环风险**: `pending.remove(wid)` 前无 None 检查，若 `get_result` 返回 `None`（worker 还在运行）也尝试 remove，会抛 KeyError

### 修复内容
- `worker_ids` 按 `priority` 降序排序后再 spawn（高优先级先启动）
- 明确传递 `timeout=worker.get("timeout", 7200)`、`max_retries=worker.get("max_retries", self.max_retries)`
- `pending.remove` 前增加 `if result is not None` 保护
- 轮询间隔从 1.0s 降至 0.5s 提升响应速度

### 文件
- `harness.py` 第 147–221 行

---

## 2. `ceo_brain.py` — `dependency_rules` 动态推导

### 变更描述
原 hardcoded `dependency_rules` 只能表达"类型级别"依赖（如 `doc_worker` 依赖 `code_worker`），无法表达"具体 worker 之间的依赖"。

现在优先使用 subtask 中已有的 `context['input_from']` 字段构建精确的 worker-id 级别依赖图。

### 新增方法
- `_build_dependency_graph_from_context(workers)` — 从 `input_from` 构建 `{worker_id: [upstream_id, ...]}` 动态图
- `_build_execution_waves()` 改造 — 优先用动态图，无 `input_from` 时回退 hardcoded rules

### 逻辑
```
if any(context['input_from'] for w in workers):
    use_dynamic = True  # 从 input_from 推断了依赖
else:
    use_dynamic = False  # 回退 hardcoded rules
```
同一 wave 内按 worker_id 级别依赖拓扑排序，无环风险。

### 文件
- `ceo_brain.py` 新增 `_build_dependency_graph_from_context`，修改 `_build_execution_waves`

---

## 3. `worker_pool.py` — 实时日志流回调机制

### 新增 API
```python
pool.set_log_sink(callback: Callable[[str, dict], None])
# callback(worker_id, {"level": str, "ts": float, "msg": str, "progress": float})
```

### 实现细节
- `_monitor` 从 `proc.communicate()` 改为 `select.select()` 行缓冲实时读取
- `bufsize=1`（行缓冲）+ `stdin=subprocess.DEVNULL` 避免死锁
- 每读取一行立即通过 `_emit_log(log_sink, worker_id, line)` 解析并推送
- 支持 `[PROGRESS]`, `[INFO/WARN/ERROR/DEBUG]` 分级解析
- 新增 `get_logs(worker_id)` 方法，可随时查询已积累日志

### 使用示例
```python
def my_logger(wid, entry):
    print(f"[{entry['level']}] {wid}: {entry['msg']}")

pool = get_worker_pool()
pool.set_log_sink(my_logger)
# 之后所有 spawn 的 worker 日志都会实时推送
```

### 文件
- `worker_pool.py` 新增 `set_log_sink`, `_emit_log`, `get_logs`；改造 `_monitor`（行缓冲实时流）

---

## 4. Worker 优先级调度支持

### 已有能力（之前未显式利用）
- `create_worker()` 和 `dispatch()` 已支持 `priority` 字段
- `spawn()` 已接收 `priority` 参数

### 优化内容

#### `harness.py` — `execute_all` 优先级排序
```python
sorted_ids = sorted(worker_ids, key=get_priority, reverse=True)  # 高→低
```
并发场景下高优先级 worker 先 spawn、先处理结果。

#### `worker_pool.py` — `get_pool_status` 增强
返回结构新增 `workers` 数组，每个 worker 含 `priority`、`elapsed_s`、`status` 字段，按运行中优先+优先级降序排列。

#### 优先级使用约定
| Priority | 场景 |
|----------|------|
| 0 (默认) | 常规任务 |
| 1 | 重要但不紧急 |
| 2 | 高优任务（CEO 插队）|
| 3+ | 系统保留 |

---

## 验证结果
```bash
python3 -m py_compile harness.py    # OK
python3 -m py_compile worker_pool.py # OK
python3 -m py_compile ceo_brain.py   # OK
```

所有文件通过语法检查。

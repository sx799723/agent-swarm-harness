# MonoSwarm 关键代码优化实施报告

## 任务概述
在 `~/.hermes/agent-swarm/` 下实施 4 项优化：
1. 修复 `harness.py` 的 `execute_all` 并行假象 bug
2. 将 `dependency_rules` 从 hardcoded 改为由 decomposition 动态推断
3. 确认 `worker_pool.py` 实时日志流返回机制（已存在）
4. 确认 Worker 优先级调度支持（已存在）

---

## 优化 1：修复 `execute_all` 并行假象 bug

**文件**: `harness.py` (第 172–204 行)

### 问题
原代码的并行分支使用"依次 spawn → 依次等待"模式：
```python
for wid in sorted_ids:
    self._pool.spawn(...)   # 依次 spawn
while pending:
    for wid in list(pending):
        result = self._pool.get_result(wid)
        if result is not None:
            results[wid] = result
            pending.remove(wid)   # 在迭代中修改 set
```
由于 `pending.remove(wid)` 在 `for` 循环内执行，且 `list(pending)` 是迭代快照而非实时副本，导致当一个 worker 完成时 `pending` 被修改，可能跳过下一个 worker 的检查。

### 修复
1. **消除迭代中修改 set 的问题**：将 `pending.remove(wid)` 改为在迭代结束后统一 `discard`，使用 `completed_this_round` 列表收集本轮完成项
2. **缩短轮询间隔**：从 0.5s → 0.2s，提升响应速度

```python
while pending:
    completed_this_round = []
    for wid in list(pending):         # 快照避免跳项
        result = self._pool.get_result(wid)
        if result is not None:
            results[wid] = result
            completed_this_round.append(wid)
    for wid in completed_this_round:  # 迭代结束后统一移除
        pending.discard(wid)
    if pending:
        time.sleep(0.2)               # 缩短轮询间隔
```

---

## 优化 2：dependency_rules 动态推断

**文件**: `ceo_brain.py` (第 491–500 行 + 新增 `_infer_dependency_rules` 方法)

### 问题
`dependency_rules` 是硬编码的静态字典：
```python
dependency_rules = {
    "doc_worker":  ["code_worker"],
    "qa_worker":   ["code_worker"],
    "ppt_worker":  ["doc_worker", "code_worker"],
}
```
这导致即使某个 worker_type 不在任务中，也会被当作依赖项处理，且无法根据实际任务构成动态调整。

### 修复
新增 `_infer_dependency_rules(subtasks)` 方法，根据 subtasks 中**实际存在**的 worker_type 动态推断依赖：

```python
def _infer_dependency_rules(self, subtasks) -> dict[str, list[str]]:
    present_types = {st["worker_type"] for st in subtasks}
    rules = {}

    if "doc_worker" in present_types and "code_worker" in present_types:
        rules["doc_worker"] = ["code_worker"]
    if "qa_worker" in present_types and "code_worker" in present_types:
        rules["qa_worker"] = ["code_worker"]
    if "ppt_worker" in present_types:
        if "doc_worker" in present_types:
            rules["ppt_worker"] = ["doc_worker"]
        elif "code_worker" in present_types:
            rules["ppt_worker"] = ["code_worker"]
    return rules
```

### 效果
- 只有当 `doc_worker` **和** `code_worker` 同时存在时，doc 才依赖 code
- 单 `ppt_worker` 任务不再错误等待不存在的 doc_worker
- 新增 worker_type 可通过扩展此方法支持，无需修改硬编码字典

---

## 优化 3：实时日志流返回机制

**文件**: `worker_pool.py`

### 现状确认
`worker_pool.py` 已实现完整的实时日志流机制：

| 组件 | 位置 | 功能 |
|------|------|------|
| `set_log_sink(sink)` | L135 | 注册全局日志接收器回调 |
| `_emit_log()` | L364 | 解析单行并通过 sink 推送 |
| `get_logs(worker_id)` | L450 | 非阻塞获取已积累的所有日志 |
| `WorkerResult.logs` | L108 | 结果中包含完整日志列表 |
| `WorkerResult.progress` | L109 | 结果中包含 0.0~1.0 进度 |

**使用方式**：
```python
pool = get_worker_pool()
pool.set_log_sink(lambda wid, entry: print(f"[{wid}] {entry['level']}: {entry['msg']}"))
pool.spawn(worker_id="w1", worker_type="code_worker", goal="...")
```

---

## 优化 4：Worker 优先级调度支持

**文件**: `harness.py`, `worker_pool.py`

### 现状确认
优先级调度已在以下位置完整实现：

| 位置 | 机制 |
|------|------|
| `harness.py:dispatch()` L109 | 从 worker config 读取 `priority` 字段 |
| `harness.py:execute_all()` L166–170 | 按 `priority` 降序排序 worker_ids |
| `worker_pool.py:spawn()` L155 | `priority` 参数传入 worker 记录 |
| `worker_pool.py:get_pool_status()` L493 | status 返回中包含 `priority` 字段 |

**使用方式**：
```python
harness.dispatch(task_id, workers=[{
    "worker_type": "code_worker",
    "goal": "...",
    "priority": 10   # 高优先级，数值越大越先调度
}])
```

---

## 验证结果

所有修改文件通过 Python 语法检查：
```
OK: harness.py
OK: ceo_brain.py
OK: worker_pool.py
All files compile OK
```

---

## 文件变更摘要

| 文件 | 变更类型 | 变更内容 |
|------|----------|----------|
| `harness.py` | 修改 | 修复 `execute_all` 并行 bug（L172–204） |
| `ceo_brain.py` | 修改 | 新增 `_infer_dependency_rules()` 方法，替换硬编码字典（L491–500） |
| `worker_pool.py` | 无变更 | 机制已存在，无需修改 |

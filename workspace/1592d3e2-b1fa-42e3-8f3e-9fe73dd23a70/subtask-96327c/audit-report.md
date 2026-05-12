# MonoSwarm 代码审计报告

审计范围：`ceo_brain.py`、`harness.py`、`worker_pool.py`、`session_store.py`

---

## 问题列表

### 🔴 Bug 1 — `worker_pool.py`: `_monitor()` 死代码/逻辑错误

**位置**: `worker_pool.py` 约 L379 — `if proc_state == 0 or proc_state is None`

**问题描述**:
`proc_state = self._workers[worker_id].proc.poll()` 在 `while True` 循环外部调用时，进程已从循环内通过 `break` 退出，`poll()` 必然返回退出码（整数），不可能返回 `None`。因此 `proc_state is None` 分支永远不可达，属于死代码。

**影响**: 逻辑分支判断冗余，代码可读性差，误导阅读者。

**修复方式**:
将状态判断提前到循环外（`proc_state = proc.poll()`），在循环正常 `break` 后直接根据 `proc_state` 判断，移除永远不可达的 `None` 分支。

---

### 🟡 Bug 2 — `worker_pool.py`: `spawn()` 传递 `timeout_seconds` 但 `Worker` 数据类缺少该字段

**位置**: `worker_pool.py` L253-264

**问题描述**:
`socket_worker()` 中 `spawn()` 传入了 `timeout_seconds` 参数，但 `Worker` 数据类只定义了 `timeout_seconds` 的文档注释，并无实际字段（Python dataclass 要求字段在类体内声明）。这会导致 `AttributeError: 'Worker' object has no attribute 'timeout_seconds'`。

**影响**: 运行时崩溃。

**修复方式**:
在 `Worker` dataclass 中添加 `timeout_seconds: int = 7200` 字段。

---

### 🟡 Bug 3 — `worker_pool.py`: `spawn()` 中 `bufsize=1` 在 text mode 下被 Python 忽略

**位置**: `worker_pool.py` L248 — `subprocess.Popen(..., bufsize=1, ...)`

**问题描述**:
Python 3.7+ 中，`subprocess.Popen` 在 `text=True`（即默认的 text mode）时，`bufsize` 参数会被 Python运行时重置为 `-1`（全缓冲），`bufsize=1`（行缓冲）的设置被忽略。注释说"行缓冲，实时读取"与实际行为不符。

**影响**: 实时日志推送可能存在延迟（非确定性），在 worker 输出量大时尤为明显。

**修复方式**: 使用默认 `bufsize=-1` 或去掉 `bufsize` 参数，依赖 Python 默认行为。

---

### 🟢 重复注释 — `ceo_brain.py`: 类文档注释块重复

**位置**: `ceo_brain.py` L149-153

**问题描述**: 文件中有两段完全相同的 `# ───────────────────────────────────────── # CEO Brain 类 # ─────────────────────────────────────────` 注释块，后者前面还有多余的空行。

**影响**: 极低（仅美观问题）。

---

## 修复记录

| 文件 | 修复内容 | 操作 |
|------|----------|------|
| `worker_pool.py` | 添加 `timeout_seconds` 和 `max_retries` 字段到 `Worker` dataclass | `patch` |
| `worker_pool.py` | 重构 `_monitor()` 末尾状态判断逻辑：提前获取 `proc_state`，移除死代码分支 | `patch` |
| `ceo_brain.py` | 删除重复的类注释块，恢复正确的 docstring | `patch` |

---

## 语法验证

```
$ python3 -m py_compile ceo_brain.py   → OK
$ python3 -m py_compile harness.py     → OK
$ python3 -m py_compile worker_pool.py → OK
$ python3 -m py_compile session_store.py → OK
```

所有文件通过 Python 语法检查（Python 3.x）。

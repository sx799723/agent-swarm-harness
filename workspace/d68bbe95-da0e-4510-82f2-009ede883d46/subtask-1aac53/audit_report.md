# MonoSwarm 代码审计报告

**审计范围**: `ceo_brain.py`, `harness.py`, `worker_pool.py`, `session_store.py`
**审计日期**: 2026-05-12
**审计维度**: 架构设计 | 已知Bug | 并发安全 | 依赖注入缺陷

---

## 一、架构设计评估

### 1.1 整体架构

```
用户 → CEOBrain.decompose() → 任务拆解
                        ↓
              AgentSwarmHarness.run()
                        ↓
          ┌─────────────┴─────────────┐
          ↓                             ↓
   execute_all (并行)           dispatch + execute
          ↓                             ↓
   WorkerPool.spawn()           WorkerPool.spawn()
   (subprocess.hermes chat)     (subprocess.hermes chat)
```

**设计亮点**:
- **Context Passing**: 通过 `input_from` 在 worker 间传递产出目录，下游 worker 可读取上游结果
- **Hand Passing**: wave 间通过 `_inject_upstream_results()` 将上游结果文本注入下游 goal
- **Wave 执行**: `_build_execution_waves()` 拓扑排序，支持动态依赖图和硬编码规则两套机制
- **Skill 动态路由**: SKILL_ROUTING 表按关键词匹配 worker 类型对应的 skill
- **可观测性**: WorkerResult 包含 logs/progress 字段，log_sink 实时推送

### 1.2 各模块职责

| 模块 | 职责 | 评价 |
|------|------|------|
| `ceo_brain.py` | 任务拆解 + wave 执行编排 + result 汇总 | 逻辑清晰，拆解策略分规则/LLM 两级 |
| `harness.py` | 调度层：dispatch → execute → retry → aggregate | 薄而专一，作为 CEO 和 WorkerPool 的桥梁 |
| `worker_pool.py` | Worker 生命周期 + subprocess 管理 + 日志解析 | 功能完整，select I/O 实时读取 |
| `session_store.py` | SQLite 持久化：task/worker/event_log | 表结构合理，有审计日志 |

### 1.3 架构问题

**P0 - 可观测性不足**:
- `_monitor` 线程中 `select.select()` 在 macOS 上对 pipe fd 不生效（见 Bug #2），导致监控线程无法读取输出，worker 实际"假死"
- `execute_all` 的轮询用 `time.sleep(0.5)` 固定间隔，不支持自适应
- 没有暴露 worker 的实时进度/状态 HTTP 端点，外部无法感知执行状态

**P1 - 伸缩性问题**:
- `session_store.py` 每次调用 `get_db()` 创建新连接，高并发下连接数快速增长
- SQLite WAL 模式未启用，写锁竞争严重（4个模块共享同一个 db 文件）
- 依赖规则硬编码，新加 worker 类型需改代码

---

## 二、已知 Bug 根因分析

### Bug #1: CEO Brain 重复注释块（ceo_brain.py:151-153）

```python
# ─────────────────────────────────────────
# CEO Brain 类
# ─────────────────────────────────────────

# ─────────────────────────────────────────
# CEO Brain 类        ← 完全重复
# ─────────────────────────────────────────
```

**根因**: 编辑器/合并工具重复插入注释块，不影响运行时行为，但说明代码审查流程缺失。

---

### Bug #2: macOS 上 `select.select()` 对 pipe fd 不生效（worker_pool.py:259）

```python
# worker_pool.py:249
import select
# ...
rlist, _, xlist = select.select([proc.stdout, proc.stderr], [], [], min(0.5, remaining))
```

**根因**: POSIX 规定 `select()` 对 pipe fd 的 readability 检测有限制（Linux 可以，macOS 不行）。macOS 上 `proc.stdout` 永远不会被标记为 readable，导致 `_monitor` 线程永远走 `rlist` 为空的分支，worker 输出无法读取。

**影响**: worker 假死——进程在跑，但没有任何输出返回，`parse_worker_output` 收到的 `stdout_text` 为空。

**复现**: 在 macOS 上执行任何 worker 任务。

---

### Bug #3: `proc_state == 0` 的判定逻辑错误（worker_pool.py:327）

```python
if proc_state == 0 or proc_state is None:
    result = WorkerResult(
        worker_id=worker_id,
        status="completed",   # ← None 也在此处设置 completed
        ...
    )
else:
    result = WorkerResult(
        worker_id=worker_id,
        status="failed",
        ...
    )
```

**根因**: `proc.poll()` 返回 `None` 表示进程尚未终止，不代表成功。在 `_monitor` 超时路径中（TimeoutError），`proc.kill()` 后 `proc_state` 应该非零，但如果 kill 失败则 poll 返回 None，导致被错误标记为 completed。

---

### Bug #4: `execute_all` 在并行模式下直接调用 `_pool.spawn` 两次（harness.py:172-189 + harness.py:117-145）

```python
# harness.py:172-189 并行分支
for wid in sorted_ids:
    self._pool.spawn(...)  # ← 第1次 spawn

# harness.py:206-224 顺序分支
for wid in sorted_ids:
    self._pool.spawn(...)  # ← 同样是第1次 spawn
    result = self._pool.wait_for_result(wid)

# BUT: execute_worker (harness.py:117-145) 也调用 _pool.spawn
def execute_worker(self, worker_id: str) -> WorkerResult:
    result = self._pool.spawn(...)  # ← 如果外部同时调用 execute_worker 和 execute_all，重复
```

**更严重的是**: `execute_all` 内部先 spawn 所有 worker 到 `spawned` 列表，然后立即进入轮询等待，但 `harness.run()` 调用路径是：

```
run() → dispatch() + execute_all()  # dispatch 已通过 create_worker 在 DB 创建记录
     → execute_all() 内直接调用 _pool.spawn()
```

而 `execute_worker()` 是一个独立的公开方法，如果外部代码同时调用 `execute_worker` 和 `execute_all`，会导致同一个 `worker_id` 被 spawn 两次。

**实际影响**: 每个 worker 实际运行两个 hermes chat 进程，资源翻倍。

---

### Bug #5: `execute_all` 复用 `spawned` 列表作为 pending set（harness.py:192-203）

```python
pending = set(spawned)  # ← spawned 是 worker_id 列表
while pending:
    for wid in list(pending):
        result = self._pool.get_result(wid)  # ← 非阻塞，立即返回 None
        if result is not None:
            ...
            pending.remove(wid)
        # 如果 result 是 None（worker 还在跑），for 循环继续检查下一个
    if pending:
        time.sleep(0.5)
```

**问题**: 这段代码存在逻辑缺陷——如果 `pending` 中的某个 worker 始终没有完成（比如卡死或被阻塞在子进程），循环会永远运行下去，因为没有退出条件。缺少最大循环次数限制或总超时机制。

---

### Bug #6: `session_store.py` 没用 WAL 模式

```python
def init_db():
    conn = get_db()
    cur = conn.cursor()
    # CREATE TABLE ...
    # 没有 PRAGMA journal_mode=WAL
    conn.commit()
```

**根因**: SQLite 默认 journal_mode=DELETE，写操作互相阻塞，高并发下性能差。

**影响**: 当 7 个 worker 同时更新 DB 时，可能出现 `database is locked` 错误。

---

### Bug #7: CEO Brain `_llm_decompose` 的 JSON 解析在超时时可能读取部分 JSON

```python
# ceo_brain.py:247-292
result = subprocess.run(
    ["hermes", "chat", "-q", prompt, "-Q"],
    capture_output=True, text=True, timeout=60,
)
# ...
json_text = output  # 如果超时，stdout 可能只有部分 JSON
decomposition = json.loads(json_text)  # ← 这里抛 JSONDecodeError，被 except 捕获并回退
```

**实际影响**: 这里的 try-except 正确处理了超时/解析失败，会回退到规则方法，所以这是个低风险设计问题：超时时的 JSON 片段会被忽略，正确回退。

---

### Bug #8: `get_db()` 无连接池，高并发耗尽连接

```python
def get_db():
    conn = sqlite3.connect(DB_PATH)  # 每次调用新连接
    return conn
```

**根因**: 没有连接池，高并发下 `get_db()` 被频繁调用（比如 `create_task` + `update_task_status` + `get_task` 等），每次都打开新连接。

**影响**: SQLite 默认最大连接数 100，高并发场景轻易超限。

---

### Bug #9: Skill 路由错误占位

```python
# ceo_brain.py:66
(["字幕", "语音", "配音"], "media/spotify"),  # ← 字幕/配音 路由到 spotify，完全错误
# ceo_brain.py:74
(["logo", "icon", "图标设计", "vi"], "creative/pixel-art"),  # ← logo/icon 路由到 pixel-art，粗糙
```

**根因**: `media/spotify` skill 是音乐播放控制，不是字幕/配音处理。Skill 路由表里的占位符没有替换成真实 skill。

---

## 三、并发安全性检查

### 3.1 session_store.py — SQLite 并发

| 检查项 | 结果 | 说明 |
|--------|------|------|
| 连接管理 | ⚠️ 差 | 每次调用 `get_db()` 新建连接，无连接池 |
| WAL 模式 | ❌ 未启用 | 默认 rollback journal，高并发写阻塞 |
| 事务隔离 | ✅ 原子性 | 单条 SQL 语句本身是原子的 |
| 参数化查询 | ✅ 安全 | 所有用户数据通过 `?` 参数绑定，无 SQL 注入 |
| 并发写入 | ⚠️ 风险 | 7 个 worker + harness + CEO 同时写，`database is locked` 概率高 |

### 3.2 worker_pool.py — 线程安全

| 检查项 | 结果 | 说明 |
|--------|------|------|
| `self._workers` 访问 | ✅ 有锁 | `self._lock` 保护所有读写 |
| `_monitor` 线程安全 | ⚠️ 部分 | `_emit_log` 在持有锁后调用，但 `log_sink` 是外部回调，可能阻塞或死锁 |
| daemon 线程 | ⚠️ 注意 | `threading.Thread(daemon=True)`，主进程退出时监控线程被强制终止，不保证清理 |
| `get_worker_pool()` 单例 | ✅ 安全 | `threading.Lock()` 双重检查 |
| subprocess I/O | ⚠️ macOS | `select.select()` 对 pipe fd 在 macOS 不生效（Bug #2）|

### 3.3 harness.py — 线程安全

| 检查项 | 结果 | 说明 |
|--------|------|------|
| `ThreadPoolExecutor` | ✅ 内部管理 | Python 标准库线程安全 |
| `execute_all` 轮询 | ⚠️ 无超时 | pending 循环没有最大迭代次数或总超时（Bug #5）|
| 多线程并发调用 | ⚠️ 风险 | `execute_worker` 和 `execute_all` 可能同时操作同一 worker_id |

### 3.4 CEO Brain — 无锁但无共享状态

| 检查项 | 结果 | 说明 |
|--------|------|------|
| `execute()` wave 循环 | ✅ 无竞争 | wave 间天然串行 |
| `all_results` 累积 | ✅ 无竞争 | 单线程顺序更新 |
| `_inject_upstream_results` | ✅ 无竞争 | 只读文件 |

---

## 四、依赖注入机制缺陷

### 4.1 硬编码 dependency_rules 覆盖不全

```python
# ceo_brain.py:496
dependency_rules = {
    "doc_worker":  ["code_worker"],
    "qa_worker":   ["code_worker"],
    "ppt_worker":  ["doc_worker", "code_worker"],
}
```

**缺陷**:
- `video_worker`, `ui_worker`, `research_worker`, `generic_worker` 不在此表中
- 但 `_build_execution_waves()` 有兜底逻辑：如果 `use_dynamic=True` 则用 `input_from` 构建动态图
- **实际问题**: 当没有 `input_from` 时（简单任务），系统退化为 hardcoded 规则，可能导致依赖顺序错误

### 4.2 动态依赖图 vs 硬编码规则的冲突

```python
# ceo_brain.py:614-665
use_dynamic = any(w.get("context", {}).get("input_from") for w in workers)
if use_dynamic:
    dynamic_graph = self._build_dependency_graph_from_context(workers)
else:
    dynamic_graph = {}
    # 回退到 hardcoded
```

**场景**: 假设一个任务触发了 `doc_worker` + `research_worker`，且 doc 需要 research 的数据。
- `doc_worker` 的 `input_from` 里应该有 `research_worker`
- 但 `dependency_rules` 里 `doc_worker` 只依赖 `code_worker`
- 如果 `input_from` 存在（动态），`dynamic_graph` 被使用，research 在 doc 之前 → 正确
- 如果 `input_from` 为空（简单任务），hardcoded 规则生效，`doc_worker` 不等待任何东西 → **错误**

### 4.3 result 注入 — 路径遍历风险

```python
# ceo_brain.py:688-689
files = glob.glob(os.path.join(up_output_dir, "*"))
# → ceo_brain.py:699
result_text = up_result["result"][:2000]
```

- `up_output_dir` 来自 `input_from`，是 CEO 在构建 context 时设定的路径
- 如果 `input_from` 被注入恶意路径（比如 `../../etc/passwd`），glob 可能越界
- **风险评级**: 低（因为 `input_from` 是 CEO 自己构建的，不来自用户输入）

### 4.4 没有依赖超时机制

```python
# ceo_brain.py:555
wave_result = self.harness.run(task_id, wave_workers, parallel=True)
# 如果某个上游 worker 永远卡住，下游 wave 永远不会被触发
```

**问题**: 上游 worker 失败或卡死时，`execute_all` 的 `pending` 循环没有总超时，整个 CEO 执行会挂起。

---

## 五、总结评分

| 维度 | 评分 | 说明 |
|------|------|------|
| 架构设计 | 8/10 | 分层清晰，Context Passing 设计巧妙，但可观测性不足 |
| 代码质量 | 6/10 | Bug #2 和 Bug #4 是生产级隐患，其余多为工程粗糙 |
| 并发安全 | 6/10 | SQLite WAL 缺失是主要瓶颈，macOS select 是阻断级 Bug |
| 依赖注入 | 7/10 | 动态图机制好，但硬编码规则回退有逻辑漏洞 |
| 安全审计 | 8/10 | SQL 注入防护好，subprocess shell=True 有风险但可控 |
| 可测试性 | 7/10 | 有单元测试目录，但核心调度逻辑缺少集成测试 |

**最高优先级修复建议**:
1. 🔴 Bug #2（macOS select）：改用 `threading` 或 `asyncio` 替代 `select.select()` 读取 pipe
2. 🔴 Bug #4（双重 spawn）：重构 `execute_all`，统一 spawn 调用路径
3. 🟡 Bug #6（WAL 模式）：`init_db()` 加上 `PRAGMA journal_mode=WAL`
4. 🟡 Bug #5（pending 循环无超时）：加上 `max_iterations` 或 `total_timeout`
5. 🟢 Bug #9（skill 路由错误）：替换占位符为真实 skill 路径

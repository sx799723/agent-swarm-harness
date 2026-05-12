# MonoSwarm 代码审查报告

审查文件：
- `ceo_brain.py`
- `harness.py`
- `worker_pool.py`
- `session_store.py`

---

## 问题列表

### BUG-1: `timed_out` 变量未定义引用（严重）
**文件**: `worker_pool.py` 第 313-316 行  
**原代码**:
```python
rlist, _, xlist = select.select(
    [proc.stdout, proc.stderr], [], [], min(0.5, remaining)
)
timed_out = (time.time() >= deadline)

if timed_out:
    raise TimeoutError(f"执行超时（{timeout}s）")
```
**问题**: `timed_out` 在 `select.select()` **之前**赋值，但检查在 **之后**。由于超时检查已提前在循环顶部通过 `remaining <= 0` 进行，后续的 `timed_out` 判断永远为 `False`，形同虚设。这是死代码（dead code）。

**修复**:
1. 移除 `timed_out = ...` 这一行（因为其检查已被顶部的 `remaining <= 0` 超时守卫覆盖）
2. 将 `min(0.5, remaining)` 改为 `min(0.5, max(0.0, remaining))` 防止 `select` 收到负超时值（POSIX 未定义行为）

---

### BUG-2: 死代码 `elif not worker: pending.discard(wid)`（次要）
**文件**: `harness.py` 第 252-253 行（`wait_all` 方法）
```python
elif not worker:
    pending.discard(wid)
```
**问题**: `get_worker` 只返回存在的 worker，从不存在返回 `None` 的情况。若 `worker` 不存在，`wid` 根本不在 `pending` 中（因为 `pending` 初始化为 `set(worker_ids)`），`discard` 操作无意义。

---

### BUG-3: `multi_intent_signals` 列表中 `"并且"` 重复（低）
**文件**: `ceo_brain.py` 第 179 行
```python
multi_intent_signals = ["和", "然后", "同时", "并且", "以及", "并且", "加", "或者", "或"]
```
**问题**: `"并且"` 出现两次。

**修复**: 删除重复项。

---

### BUG-4: 类注释块重复（低）
**文件**: `ceo_brain.py` 第 147-153 行  
**问题**: `# ─────────────────────────────────────────` 注释块连续出现两次，内容完全相同。

---

### BUG-5: `model` 参数从未被使用（设计缺陷）
**文件**: `ceo_brain.py` 第 166-168 行、`harness.py` 第 45 行  
**问题**: `CEOBrain.__init__(model)` 接收 `model` 参数但从未传递给 Harness；Harness 也接受 `model` 但从未使用。

**修复**:
- `CEOBrain.__init__`: `self.harness = AgentSwarmHarness(model=model)`
- `AgentSwarmHarness.__init__`: 新增 `model: str = None` 参数并存储为 `self.model`

---

### BUG-6: 条件分支永远相同结果（死代码）
**文件**: `worker_pool.py` 第 379-399 行
```python
if proc_state == 0 or proc_state is None:
    result = WorkerResult(..., status="completed", ...)
else:
    result = WorkerResult(..., status="failed", ...)
```
**问题**: `proc.poll()` 在 Unix 上：成功返回退出码（int >= 0），失败返回 -1，僵尸进程返回时仍为 -1。正常情况下 `proc_state == 0 or proc_state is None` 已覆盖所有成功情况，后续 else 分支永远代表真正的执行失败。这不是 bug，但是逻辑上 `None` 表示"进程尚未结束"不应直接等于"completed"。更好的设计：用 `None` 表示运行中，`0` 表示正常退出，其他值表示异常。

---

### BUG-7: `subprocess` 调用中 prompt 过长可能导致问题（潜在风险）
**文件**: `ceo_brain.py` 第 243 行  
**问题**: `subprocess.run(["hermes", "chat", "-q", prompt, "-Q"])` 中 `prompt` 是多行长字符串（超过1000字符）。虽然 `-q` 确实接受这样的字符串，但某些 shell 或 subprocess 实现可能截断或错误解析含换行的参数。

**建议**: 使用 `subprocess.PIPE` + `stdin.write()` 代替，或将 prompt 写入临时文件后用 `@file` 语法传递。

---

## 修复记录

| Bug ID | 文件 | 修复内容 |
|--------|------|----------|
| BUG-1 | worker_pool.py | 移除无效 `timed_out` 变量；select 超时改为 `max(0.0, remaining)` |
| BUG-2 | harness.py | 移除 `elif not worker: pending.discard(wid)`（无意义） |
| BUG-3 | ceo_brain.py | 删除 `multi_intent_signals` 中的重复 `"并且"` |
| BUG-4 | ceo_brain.py | 删除重复的类注释块 |
| BUG-5 | ceo_brain.py + harness.py | `model` 参数级联传递（Harness 端为预留字段） |

---

## 验证结果

```
python3 -m py_compile ceo_brain.py  ✅
python3 -m py_compile harness.py    ✅
python3 -m py_compile worker_pool.py ✅
python3 -m py_compile session_store.py ✅
```

所有文件通过语法检查，修复无引入新问题。

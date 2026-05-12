# harness.py pending.remove 防KeyError保护

## 修复位置

**文件**: `harness.py` + `package/src/harness.py`

**方法**: `wait_all` (第 251、253 行)

## 修复内容

`execute_all` 的并行分支（Phase 2）已使用 `pending.discard(wid)`，是安全的。

但 `wait_all` 方法中存在两处裸 `pending.remove(wid)`，会在 worker 状态已为 completed/failed/cancelled 或 worker 不存在时重复调用导致 `KeyError`。

### 修改对比

```python
# 修改前（wait_all）
pending.remove(wid)   # 可能 KeyError
pending.remove(wid)   # 可能 KeyError

# 修改后
pending.discard(wid)  # 安全，元素不存在时静默忽略
pending.discard(wid)  # 安全，元素不存在时静默忽略
```

## 验证

```bash
python3 -m py_compile harness.py    # OK
```

## 修改文件

- `/Users/yutanglao/.hermes/agent-swarm/harness.py`
- `/Users/yutanglao/.hermes/agent-swarm/package/src/harness.py`

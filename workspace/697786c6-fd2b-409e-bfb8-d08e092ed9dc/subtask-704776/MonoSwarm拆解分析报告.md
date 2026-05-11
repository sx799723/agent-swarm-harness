# MonoSwarm 拆解分析报告
**CEO 级评估报告**  
**分析日期**: 2026-05-12  
**报告级别**: 战略评估 · 机密

---

## 一、项目简介

**MonoSwarm** 是一个轻量级 **任务拆解 + 并发调度** 框架，定位为 Hermes Agent 的"CEO Brain"——负责理解用户意图、拆解子任务、调度 Worker 并行执行、汇总结果输出。

### 核心定位

```
用户自然语言任务
      │
      ▼
  CEOBrain          ← 任务理解 + 拆解
      │
      ├─► Harness            ← 并发调度 + 重试 + 持久化
      │       │
      │       └─► WorkerPool ← 子进程管理
      │               │
      │               └─► hermes chat CLI（外部 Agent）
      │
      ▼
  汇总报告（Markdown）
```

### 技术栈

| 层级 | 技术 |
|------|------|
| 入口 | Python CLI (`run.py`) |
| 调度核心 | Python stdlib (`concurrent.futures`) |
| 持久化 | SQLite（`swarm.db`） |
| 执行单元 | `subprocess.Popen` → `hermes chat` |
| Agent | 外部 Hermes CLI（不在本项目内） |

**依赖哲学**: 极简主义——仅使用 Python 标准库，把复杂推理委托给外部 Hermes Agent。

---

## 二、架构总览

### 2.1 核心模块

| 模块 | 文件 | 职责 | 代码行数(估) |
|------|------|------|-------------|
| CEO Brain | `ceo_brain.py` | 任务理解 → 拆解 → 汇总 | ~400 |
| Harness | `harness.py` | 分发/执行/重试/汇总 | ~300 |
| WorkerPool | `worker_pool.py` | Worker 生命周期管理 | ~150 |
| SessionStore | `session_store.py` | SQLite 持久化 | ~200 |
| CLI | `run.py` | 用户入口 | ~100 |

### 2.2 调用链路

```
用户: python3 run.py "任务描述"
  │
  ▼
CEOBrain.run_full_flow()
  │
  ├─► decompose()        # 关键词规则拆解 → 7种 worker 类型
  │
  ├─► execute()          # 调度执行
  │       │
  │       └─► Harness.run()
  │               │
  │               ├─► dispatch()        # 创建 Worker 记录，写入 SQLite
  │               ├─► execute_all()     # ThreadPoolExecutor 并发执行
  │               │       │
  │               │       └─► WorkerPool.spawn()
  │               │               │
  │               │               └─► subprocess.Popen("hermes chat -q ...")
  │               │
  │               └─► retry_failed()  # 失败重试（可选）
  │
  └─► aggregate()         # Markdown 结果汇总
```

### 2.3 数据模型

```
swarm.db (SQLite)
  ├─ tasks          任务表
  ├─ workers        Worker 表（status/result/error/retry_count）
  ├─ event_log      审计日志
  └─ ceo_assignments  任务-Worker 分配关系
```

**状态机**:
- Task: `pending` → `running` → `completed` / `partial` / `failed` / `cancelled`
- Worker: `pending` → `running` → `completed` / `failed` / `cancelled`

---

## 三、核心能力评估

### 3.1 任务拆解 — ⚠️ 初级可用

**现状**: `_rule_based_decompose()` — 纯关键词匹配

```python
["写", "开发", "代码", "api", "前端"]  → code_worker
["ppt", "演示", "幻灯片"]              → ppt_worker
["视频", "剪辑"]                       → video_worker
["测试", "test", "验证"]               → qa_worker
```

**Worker 类型映射**:

| Worker Type | 对应 Skill |
|-------------|-----------|
| code_worker | `software-development/skill-creator` |
| ppt_worker | `productivity/ppt-workflow` |
| video_worker | `media/youtube-content` |
| ui_worker | `creative/baoyu-comic` |
| qa_worker | `software-development/test-driven-development` |
| doc_worker | `productivity/spreadsheet` |
| generic_worker | 无 |

**评估**: 
- ✅ 支持多标签（`any()` 遍历关键词，同一任务可产出多个 subtask）
- ❌ 无语义理解，纯字符串匹配
- ❌ 代码内明确标注 `TODO: 后续替换为LLM智能拆解`
- **结论**: 仅适合简单任务，复杂任务拆解质量差

### 3.2 并发调度 — ⚠️ 表面并行

**实现**: `ThreadPoolExecutor(max_workers=3)`

```python
cmd = f"hermes chat {skill_flag} -q {json.dumps(full_goal)} --quiet"
proc = subprocess.Popen(cmd, shell=True, stdout=PIPE, stderr=PIPE, cwd=PROJECT_ROOT)
stdout, stderr = proc.communicate(timeout=7200)  # 2小时超时
```

**评估**:
- ✅ 并发数可配置（默认3）
- ✅ 支持并行/顺序切换
- ❌ subprocess 同步等待，父进程实际阻塞
- ❌ Worker 间无数据共享/结果传递机制
- ❌ 2小时超时硬编码
- **结论**: 伪并发，扩展性受限

### 3.3 重试机制 — ⚠️ 基础可用

```python
if worker["retry_count"] < worker.get("max_retries", self.max_retries):
    increment_worker_retry(wid)  # 重试计数 +1，状态重置为 pending
    to_retry.append(wid)
```

**评估**:
- ✅ 默认最大重试3次
- ✅ 失败自动重试
- ❌ 无指数退避（exponential backoff）
- ❌ 未感知错误类型（网络超时？代码错误？）
- ❌ 重试后结果直接覆盖，无历史版本
- **结论**: 基础防护有，但智能化不足

### 3.4 持久化 — ✅ 核心能力完整

**SQLite 表结构**:
- `tasks`: 任务主表（含 goal/result/error/status）
- `workers`: Worker 表（含 session_id 复用 worker_id）
- `event_log`: 状态变更审计

**评估**:
- ✅ 基础 CRUD 完整
- ✅ 索引合理（task_id/status）
- ❌ parent_id 字段存在但 Harness 未使用（无层级树）
- ❌ result 字段直接覆盖，无历史
- ❌ event_log 仅记录状态变更，无 stdout/stderr
- **结论**: 够用但不完善

### 3.5 会话管理 — ❌ 缺失

- Worker 结果通过 `session_id`（实际复用 `worker_id`）关联
- 无真正的跨 Worker 会话共享
- CEO 汇总阶段只拿到文本 `result`，无结构化数据

**结论**: 仅为"执行记录"，非真正的会话管理系统

---

## 四、不足清单（分类 + 优先级）

### 🔴 P0 — 必须修复（阻断业务）

| # | 问题 | 影响 |
|---|------|------|
| 1 | 任务拆解为纯规则匹配，无法处理复杂语义 | 拆解质量差，子任务划分不合理，实际无法用于生产 |
| 2 | subprocess 同步等待，无真正的异步并发 | 父进程阻塞，并发形同虚设 |
| 3 | 无单元测试，完全依赖手动自测 | 迭代风险极高，任何改动无法回归验证 |

### 🟠 P1 — 重要改进（影响效率）

| # | 问题 | 影响 |
|---|------|------|
| 4 | Worker 间无数据共享/结果传递机制 | 并行执行价值大幅缩水 |
| 5 | 重试无差异化策略（指数退避/错误分类） | 无效重试浪费资源 |
| 6 | CEOBrain.execute() 中 dispatch + execute_all 紧耦合 | 无法单独调度/恢复任务 |
| 7 | 无监控/告警机制 | 故障被动发现，响应滞后 |
| 8 | 2小时超时硬编码，无法动态调整 | 长任务强制中断 |

### 🟡 P2 — 优化方向（体验提升）

| # | 问题 | 影响 |
|---|------|------|
| 9 | 无真正的会话管理（session_id 复用 worker_id） | 结果追溯困难 |
| 10 | 结果仅文本汇总，无结构化数据交换 | 下游处理受限 |
| 11 | 无集成测试，依赖真实 Hermes API | 测试成本高，无法 CI/CD |
| 12 | 无任务层级（parent_id 未使用） | 任务树无法展开 |

---

## 五、进化路线图（时间轴 + 里程�）

```
2026 Q2          2026 Q3          2026 Q4          2027 Q1
─────────        ─────────        ─────────        ─────────
┌─────────┐     ┌─────────┐     ┌─────────┐     ┌─────────┐
│ v0.3    │────►│ v0.5    │────►│ v1.0    │────►│ v1.2    │
│ 基础可用 │     │ 生产可用 │     │ 规模化   │     │ 智能化  │
└─────────┘     └─────────┘     └─────────┘     └─────────┘
  │               │               │               │
  ▼               ▼               ▼               ▼
 LLM拆解         异步引擎         测试覆盖        监控告警
 单元测试        会话管理         指数退避        任务层级
                  动态超时         差异化重试
```

### Phase 1: 基础可用（2026 Q2）
**目标**: 修复 P0，确保核心链路可运行

- [ ] LLM 智能拆解（替代关键词规则）
- [ ] 补充单元测试（pytest，覆盖率 > 60%）
- [ ] 分离 dispatch/execute_all，支持任务恢复

### Phase 2: 生产可用（2026 Q3）
**目标**: 修复 P1，达到生产级稳定性

- [ ] 异步执行引擎（`asyncio` + `aiohttp`，真并发）
- [ ] 会话管理重构（独立的 Session 表）
- [ ] 差异化重试策略（错误分类 + 指数退避）
- [ ] 动态超时机制

### Phase 3: 规模化（2026 Q4）
**目标**: 支撑更大规模并行和复杂任务

- [ ] Worker 间数据共享通道
- [ ] 任务层级树（parent_id 启用）
- [ ] 监控仪表盘（任务成功率/Worker 利用率）
- [ ] 集成测试 + CI/CD 流水线

### Phase 4: 智能化（2027 Q1）
**目标**: 从"调度框架"升级为"智能工作流引擎"

- [ ] LLM 自适应拆解（根据历史数据优化）
- [ ] 多级重试策略（任务级/Worker 级/系统级）
- [ ] 预测式扩容（根据任务特征预估资源）
- [ ] 多租户/命名空间隔离

---

## 六、建议优先级排序

### 立即行动（本月）

1. **补充单元测试** — 无测试的代码无法迭代，是最高风险
2. **LLM 拆解** — 关键词规则无法处理真实业务场景

### 短期（1-3月）

3. **异步执行引擎** — 同步 subprocess 是性能瓶颈
4. **差异化重试** — 减少无效重试，节省 Hermes API 调用成本

### 中期（3-6月）

5. **会话管理重构** — 结构化结果数据
6. **监控告警** — 从被动响应转为主动发现

### 长期（6月+）

7. **任务层级 + Worker 协作** — 支撑复杂工作流
8. **自适应拆解** — 越用越聪明的拆解引擎

---

## 七、总结

MonoSwarm 的核心价值在于**任务拆解 + 并发调度**的框架设计，将复杂推理委托给 Hermes Agent，自己专注于流程编排。这种设计思路是正确的，适合作为 Hermes Agent 的"CEO 控制层"。

但当前实现处于 **POC 阶段**，工程化程度较低：

| 维度 | 评分 | 说明 |
|------|------|------|
| 架构设计 | ⭐⭐⭐⭐ | 模块划分清晰，扩展性基础良好 |
| 工程化 | ⭐⭐ | 无测试，无 CI，代码质量未验证 |
| 生产可用 | ⭐⭐ | P0 问题阻断，无法用于生产 |
| 扩展性 | ⭐⭐⭐ | 架构支持，但并发实现有缺陷 |

**一句话结论**: MonoSwarm 是一个**思路正确但实现初级**的任务调度框架，建议优先修复测试和拆解两个 P0 问题后再进入生产使用。

---

*报告生成时间: 2026-05-12*  
*分析依据: 源码拆解（`ceo_brain.py`, `harness.py`, `worker_pool.py`, `session_store.py`, `run.py`）*

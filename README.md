# MonoSwarm

**具有自主路径推理和工具调用能力的智能 Agent 调度框架**

> 「调度框架需要具备自主路径推理 + Worker要有工具调用能力」

MonoSwarm 是一个运行在本地的智能 Agent 蜂群系统。CEO Brain 负责理解复杂任务并自主规划执行路径；Worker Pool 中的每个 Worker 都具备真实的工具调用能力，能够完成实际的代码编写、文件操作、浏览器控制等工作。

---

## 核心特性

### 自主路径推理
- **CEO Brain** 能够理解复杂任务背后的真实意图
- 自动分析子任务间的依赖关系，动态规划最优执行路径
- LLM 智能拆解：遇到多意图信号（和/然后/同时）或无明确类型时自动触发
- 规则快速匹配：简单任务用关键词秒拆

### 工具调用能力
- Worker 不是"输出文本描述"的复读机
- 每个 Worker 能调用真实工具：写文件、执行命令、浏览器操作、API调用
- code_worker 使用 `code-execution` skill，实际修改代码而非输出 diff
- 任务完成即销毁，不保留僵尸进程

### 真实并行执行
- 基于 ThreadPoolExecutor + 独立子进程监控
- 最多 7 个 Worker 并行执行
- spawn() 异步启动不等待，poll() 非阻塞轮询完成状态
- 支持 Wave 机制：按依赖关系分批执行（Wave1 并行 → Wave2 接力）

### 可观测性
- 完整 event_log 链路：dispatch → spawn → start → execute → complete/fail/timeout → aggregate
- 每条日志记录：task_id、worker_id、时间戳、具体事件类型和上下文
- SQLite 持久化，可随时查询任务执行历史

---

## 架构

```
用户 ──► Monica（CEO Brain）
              │
              ▼
         MonoSwarm 调度层
         (harness.py)
              │
       ┌──────┼──────┐
       ▼      ▼      ▼
   Worker  Worker  Worker   ... 最多7个并行
  (code)  (doc)  (research)
       │      │      │
       └──────┼──────┘
              ▼
         执行结果汇总
              │
              ▼
         Monica ──► 用户
```

---

## 快速开始

### 触发方式
用户直接说自然语言任务，Monica 自动判断是否需要动用 MonoSwarm。

### 示例任务
```
帮我审计 ~/.hermes/agent-swarm/ 代码，找出所有潜在bug
```
↓
```
[CEO] 理解任务 → 拆解为 3 个并行 subtask
  - code_worker: 语法检查
  - qa_worker: 逻辑审查
  - generic_worker: 文档检查
[Workers] 并行执行 → 汇总报告 → Monica → 用户
```

---

## 版本历史

| 版本 | 功能 |
|------|------|
| v0.2 | CEO智能拆解（每个Worker专注goal） |
| v0.3 | 真正多Worker并行执行（OS级并发） |
| v0.4 | Worker Context Passing + Hand Passing协作机制 |
| v0.5 | Skills动态路由层 |
| v0.6 | 可观测性升级 + 调度能力升级 |
| v0.7 | code_execution skill + 自我审计修复Bug |
| v0.8 | 确立整体基调：自主路径推理 + 工具调用能力 |

---

## 项目信息

- **GitHub**: https://github.com/sx799723/agent-swarm-harness
- **分支**: main（干净存档）+ dev-v0.1（开发分支）
- **本地路径**: `~/.hermes/agent-swarm/`
- **核心知识库**: `~/.hermes/skills/monoswarm-core/SKILL.md`

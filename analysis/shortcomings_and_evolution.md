# MonoSwarm 不足分析与进化路径建议

**分析日期**：2026-05-12
**分析依据**：ceo_brain.py、harness.py、worker_pool.py、session_store.py、run.py 源码 + 测试报告
**面向**：技术决策者 / 架构负责人

---

## 一、功能层面的不足

### 1.1 任务拆解质量差（规则匹配脆弱）
**优先级：P0**

**现状**：`ceo_brain.py` 第 294-411 行，`_rule_based_decompose()` 依赖关键词匹配：

```python
needs_code = any(kw in task_lower for kw in ["写代码", "开发代码", ...])
needs_ppt  = any(kw in task_lower for kw in ["ppt", "演示", "幻灯片", ...])
```

**具体问题**：
- "做PPT" 会被 `needs_ui=True` 误判（"做"字触发 ui 判断）
- "开发一个Python脚本然后做PPT" 中，PPT 的判定依赖 `code_exclude` 的负向逻辑，容易被复杂句式绕过
- "帮我查一下这个bug" 被识别为 generic_worker（无 code 相关动词时，即使有"bug"也不算开发）
- 多意图任务（如"写代码+做PPT+写文档"）的拆解结果是简单拼接，无法感知任务间的语义关联

**源码佐证**：`SKILL_ROUTING`（第 34-91 行）硬编码在代码中，修改需改源码而非配置。

**进化路径建议**：

| 建议 | 实现代价 | 预期收益 | 关键里程碑 |
|------|---------|---------|-----------|
| **A. LLM 智能拆解升级**：将 `_llm_decompose()` 作为默认路径，rule-based 仅做 fallback | 中（已有实现，需调优 prompt） | 高（拆解准确率提升至 85%+） | M1: LLM 拆解成功率 > 80%；M2: rule fallback 覆盖率 < 5% |
| **B. Skill 路由外置**：将 `SKILL_ROUTING` 和 `WORKER_DEFAULT_SKILLS` 从代码迁移至 YAML/JSON 配置 | 低（纯数据迁移，无逻辑改动） | 中（运营灵活度提升，无需发版即可调路由） | M1: 路由表可运行时加载；M2: 支持热更新 |
| **C. 拆解质量评估机制**：对每次拆解结果做自我校验（LLM 二次确认或规则兜底检测） | 中（需引入额外 LLM 调用） | 中（减少错误拆解的下游损失） | M1: 误判率降低 30%；M2: 拆解可追溯可回滚 |

---

### 1.2 路由机制缺乏语义理解
**优先级：P1**

**现状**：路由完全基于关键词共现（`any(kw.lower() in goal_lower for kw in keywords)`），无语义消歧能力。

**具体问题**：
- "测试" 既指向 `qa_worker`（质量保证）也指向 `dogfood`（UI 测试），但实际只能匹配第一个
- "搜索" 在 research_worker 中匹配 `find-skills-skill/find-skills`，但"搜索代码"应匹配 `sn-search-code`，当前实现用 elif 无法同时满足
- 同一个词在不同 worker_type 下的语义需要上下文消歧，当前无此能力

**进化路径建议**：

| 建议 | 实现代价 | 预期收益 | 关键里程碑 |
|------|---------|---------|-----------|
| **D. 多 skill 候选 + 评分机制**：替换 any() 匹配为多 skill 候选列表，按上下文相关性排序 | 中（需重构匹配逻辑） | 高（支持同时触发多个 skill） | M1: 支持 1 task → N skills（1:N）；M2: 准确率 > 75% |
| **E. 意图分类器**：训练/引入轻量意图分类模型，对"开发/测试/设计/调研"做显式分类 | 高（需引入 ML 能力） | 高（语义理解质的提升） | M1: 原型验证；M2: 生产级准确率 |

---

### 1.3 Context Passing 机制不完善
**优先级：P1**

**现状**：CEO Brain 第 630-673 行实现 `_inject_upstream_results()`，通过文件 glob 读取上游产出：

```python
files = glob.glob(os.path.join(up_output_dir, "*"))
```

**具体问题**：
- 依赖 Worker 主动将结果写入 output_dir，无强制性契约
- 如果 Worker 未写文件，下游收到空列表也不会报错
- 无 schema 约束——上游产出 JSON 还是文本？下游能否正确解析？
- "Hand Passing" 信息直接拼接到 goal 字符串中（第 672 行），对 Worker 而言是隐式依赖，无显式声明

**进化路径建议**：

| 建议 | 实现代价 | 预期收益 | 关键里程碑 |
|------|---------|---------|-----------|
| **F. Context 契约声明**：要求 Worker 输出时声明 schema（JSON schema 或文件 manifest） | 中（需定义契约格式 + Worker 侧适配） | 高（下游解析成功率提升，下游报错可定位） | M1: manifest.json 格式定义；M2: 契约校验在 Harness 层完成 |
| **G. 强制产出检查**：Worker 完成后 Harness 检查 output_dir 是否包含预期产出文件，无文件则标记失败 | 低（ Harness 层改动） | 中（早发现早失败，避免下游空等） | M1: 产出文件数 < 预期时告警；M2: 纳入 WorkerResult status 判定 |

---

## 二、性能层面的不足

### 2.1 并发模型效率问题
**优先级：P0**

**现状**：`harness.py` 第 147-211 行 `execute_all()` 实现并行执行：

```python
for wid in worker_ids:
    self._pool.spawn(...)  # 全部先 spawn
while pending:
    for wid in list(pending):
        result = self._pool.get_result(wid)  # 轮询
```

**关键问题**：

**问题 1 — 轮询效率低**：
- `get_result()` 是非阻塞查询，但轮询间隔固定 1 秒（第 190 行 `time.sleep(1.0)`）
- 100 个 Worker 同时运行时，每秒产生 100 次无意义查询
- 应改为事件驱动（回调通知）或按需轮询（结果就绪前指数退避）

**问题 2 — subprocess 等待方式**：
- `worker_pool.py` 第 222 行 `proc.communicate(timeout=timeout)` 是同步阻塞调用
- `_monitor()` 虽然在独立线程中运行（第 198-203 行），但无法向 Harness 层推送实时进度
- Worker 的 stdout/stderr 在 `_monitor` 完成后才能被解析（第 223 行），Worker 运行期间 Harness 无法获取中间输出

**性能数据（基于 test_performance.py）**：
- `test_harness_parallel_execution`：20 个 100ms Worker 并行完成耗时 ~0.3s（理想），但这是 Mock 性能
- 真实场景中，subprocess 启动开销（shell 解析、hermes 进程拉起）约 200-500ms/worker
- 100 并发 Worker 场景下，subprocess 竞争 + 轮询风暴是明显瓶颈

**进化路径建议**：

| 建议 | 实现代价 | 预期收益 | 关键里程碑 |
|------|---------|---------|-----------|
| **H. 事件驱动替代轮询**：Worker 完成时通过回调通知 Harness，消除固定间隔轮询 | 中（改造 WorkerPool 回调机制） | 高（CPU 使用率降低，响应延迟从 ~1s 降至 <100ms） | M1: 回调注册机制上线；M2: 移除 sleep 轮询 |
| **I. Worker 进程池预热**：对高频使用的 worker_type 预启动常驻进程，避免每次 spawn 冷启动开销 | 中（进程池管理 + 生命周期） | 高（端到端延迟降低 200-500ms/worker） | M1: 预热池容量可配置；M2: 空闲回收策略 |
| **J. 流式输出捕获**：用 `asyncio` + `subprocess` 实时捕获 Worker stdout/stderr，通过 WebSocket 推送 | 高（需引入异步框架） | 高（实时日志可见，长任务可追踪） | M1: stderr 实时流；M2: stdout 结构化解析 |

---

### 2.2 资源管理粗粒度
**优先级：P1**

**现状**：
- `worker_pool.py` 第 139 行：`timeout: int = 7200`（2小时硬编码）
- `harness.py` 第 45 行：`max_concurrent: int = 7`（固定 7 并发）
- 无 Worker 级别的资源配额（CPU/内存/网络）

**具体问题**：
- code_worker 可能运行 2 小时，qa_worker 5 分钟，但共享同一超时配置
- 7 并发在多租户场景下无法动态调整
- Worker 崩溃时资源是否泄露？（答案：会的，`proc.terminate()` 后 `proc.wait(timeout=5)` 可能导致僵尸进程）

**进化路径建议**：

| 建议 | 实现代价 | 预期收益 | 关键里程碑 |
|------|---------|---------|-----------|
| **K. Worker 级超时差异化**：按 worker_type 配置不同的 timeout（如 code_worker=7200s, qa_worker=1800s） | 低（配置表 + 传参） | 中（资源浪费减少） | M1: 超时配置表外部化；M2: 超时后优雅降级 |
| **L. 动态并发控制**：基于系统负载（CPU/内存）动态调整 max_concurrent | 中（需监控采集） | 高（多任务并发时系统稳定性提升） | M1: 基础指标采集；M2: 自动扩缩容策略 |
| **M. 进程回收机制**：对 terminate() 后的进程做 waitpid() 避免僵尸进程 | 低（单点修复） | 低（但避免资源泄露） | M1: waitpid 显式调用；M2: 资源追踪表 |

---

## 三、架构层面的不足

### 3.1 可观测性缺失
**优先级：P0**

**现状**：
- `session_store.py` 第 67-76 行有 `event_log` 表，但仅记录状态变更事件
- `worker_pool.py` 第 58-92 行 `parse_worker_output()` 能解析 `[INFO]/[WARN]/[ERROR]` 分级日志
- **但没有任何地方暴露 metrics**，无法对接 Prometheus/Grafana

**具体问题**：
- 任务成功率、平均执行时长、P95/P99 延迟 —— 全部未知
- 告警：无（任务失败了用户只能手动查状态）
- 链路追踪：无（一个任务跨多个 Worker，出问题无法定位在哪一步）

**进化路径建议**：

| 建议 | 实现代价 | 预期收益 | 关键里程碑 |
|------|---------|---------|-----------|
| **N. Prometheus Metrics 暴露**：在 harness.py 和 worker_pool.py 中埋点，暴露 `swarm_tasks_total`、`swarm_workers_running`、`swarm_task_duration_seconds` | 低（prometheus_client 库，约 200 行代码） | 高（可观测性从 0 到 1，监控体系建设基础） | M1: 核心 metrics 上线；M2: Grafana dashboard |
| **O. 结构化日志 + distributed tracing**：统一日志格式（JSON），注入 trace_id 贯穿 CEO→Harness→Worker→SQLite | 中（需日志格式重构 + trace_id 传递） | 高（故障定位效率提升 10x） | M1: JSON 日志格式；M2: trace_id 跨进程传递 |
| **P. 健康检查 API**：提供 `/health`、`/metrics` 端点，支持 K8s 探活 | 低（Flask 路由） | 中（部署友好度提升） | M1: /health 返回 200；M2: /metrics 返回 Prometheus 格式 |

---

### 3.2 分布式支持缺失
**优先级：P1**

**现状**：所有 Worker 都在单机通过 `subprocess.Popen` 启动（第 176-183 行），无法跨机器调度。

**具体问题**：
- 单机资源上限 = 最大并发数 × 单 Worker 资源占用
- 如果 Worker 是 CPU 密集型（代码编译/测试），单机扩展有上限
- 无法做跨区域调度（用户任务在 macOS，Worker 池在 Linux 服务器）

**进化路径建议**：

| 建议 | 实现代价 | 预期收益 | 关键里程碑 |
|------|---------|---------|-----------|
| **Q. 消息队列解耦**：引入 RabbitMQ/Redis Queue，将 Worker 执行从本地 subprocess 改为远程任务分发 | 高（架构重构 + 运维成本） | 高（水平扩展能力，从单机到集群） | M1: 本地队列模式（仍用 subprocess）；M2: 远程 Worker 模式 |
| **R. Docker 容器化 Worker**：Worker 以容器方式执行，环境隔离 + 资源限制 | 中（Dockerfile + 镜像构建） | 高（环境一致性，资源隔离） | M1: Worker 镜像构建成功；M2: K8s 调度验证 |
| **S. 分布式存储替换 SQLite**：PostgreSQL + 主从复制，支持多 Harness 实例并发写入 | 高（数据迁移 + 连接池管理） | 高（高可用，跨实例共享状态） | M1: PostgreSQL 迁移脚本；M2: 主从复制验证 |

---

### 3.3 架构可扩展性问题
**优先级：P2**

**现状**：
- `ceo_brain.py` 硬编码 8 种 worker_type（code/ppt/video/ui/qa/doc/research/generic）
- skill 到 worker_type 的映射在两处定义（`ceo_brain.py` 第 94-103 行 + `worker_pool.py` 第 36-45 行），存在不一致风险
- 无 plugin 机制，新增 worker_type 需要改源码

**具体问题**：
- `WORKER_TYPES` 在 `ceo_brain.py` 第 24 行被 import，但该常量在 ceo_brain.py 中并未显式定义（测试文件引用了它但源码中缺失）
- 两处 skill mapping 必须保持同步，人工维护容易出错

**进化路径建议**：

| 建议 | 实现代价 | 预期收益 | 关键里程碑 |
|------|---------|---------|-----------|
| **T. Worker Type Registry**：所有 worker_type 和其对应 skill 在单一 YAML 中定义，代码作为单一数据源 | 低（YAML + 加载器） | 中（消除不一致，降低维护成本） | M1: Registry YAML 文件；M2: 旧 mapping 代码清除 |
| **U. Plugin 架构**：允许外部 Python 模块注册新的 worker_type 和 skill 路由规则 | 中（入口点注册机制） | 高（生态扩展能力） | M1: 最小 plugin 示例；M2: plugin 沙箱隔离 |
| **V. 外部 Skill 版本锁定**：skill 版本信息写入 task metadata，确保同一任务在不同时间执行使用相同 skill 版本 | 低（manifest 扩展） | 中（行为一致性） | M1: skill 版本记录；M2: 版本回滚支持 |

---

## 四、测试覆盖不足
**优先级：P0**

**现状**：
- `tests/test_ceo_brain.py`：176 行，测试 decompose 和 mock execute，覆盖率较全
- `tests/test_harness.py`：255 行，测试 dispatch/execute/retry，覆盖较全
- `tests/test_performance.py`：207 行，并发压测，有 Mock 无真实 subprocess 测试
- `tests/test_session_store.py`：存在（但未读取到内容）
- **关键缺失**：
  - 无 CEO Brain 真实执行测试（都是 mock harness）
  - 无端到端测试（真实 subprocess 调用）
  - 无 DAG 依赖执行测试（wave 场景）
  - 无 Worker 失败重试的端到端验证

**进化路径建议**：

| 建议 | 实现代价 | 预期收益 | 关键里程碑 |
|------|---------|---------|-----------|
| **W. 单元测试补全**：CEO brain 真实调用（不 mock harness）+ Worker 真实执行路径 + DAG wave 场景 | 中（补充测试用例） | 高（迭代安全网） | M1: CEO 真实调用测试；M2: wave 依赖测试 |
| **X. 集成测试框架**：用真实 subprocess 启动 hermes chat 但短 timeout（如 5s），验证端到端链路 | 中（测试环境隔离） | 高（真实环境覆盖） | M1: 集成测试框架搭建；M2: CI 集成 |
| **Y. 模糊测试**：对 decompose() 输入随机化（emoji、特殊字符、超长输入、空输入），验证容错 | 低（随机输入生成） | 中（健壮性提升） | M1: 边界输入覆盖 > 20 种；M2: 崩溃率 < 1% |

---

## 五、优先级汇总与路线图

### P0（立即处理，影响生产可用性）

| # | 不足 | 建议 | 实现代价 | 预期收益 | 关键里程碑 |
|---|------|------|---------|---------|-----------|
| 1 | 任务拆解质量差 | A. LLM 智能拆解升级 | 中 | 高 | M1: LLM 拆解成功率 > 80% |
| 2 | 可观测性缺失 | N. Prometheus Metrics 暴露 | 低 | 高 | M1: 核心 metrics 上线 |
| 3 | 测试覆盖不足 | W. 单元测试补全 + X. 集成测试 | 中 | 高 | M1: CEO 真实调用测试 |
| 4 | 并发模型效率低 | H. 事件驱动替代轮询 | 中 | 高 | M1: 回调机制上线 |

### P1（建议 1 个月内处理）

| # | 不足 | 建议 | 实现代价 | 预期收益 | 关键里程碑 |
|---|------|------|---------|---------|-----------|
| 5 | 路由机制缺乏语义 | D. 多 skill 候选机制 | 中 | 高 | M1: 1:N skill 路由 |
| 6 | Context Passing 不完善 | F. Context 契约声明 | 中 | 高 | M1: manifest.json 格式定义 |
| 7 | 资源管理粗粒度 | K. Worker 级超时差异化 | 低 | 中 | M1: 超时配置表外部化 |
| 8 | 分布式支持缺失 | Q. 消息队列解耦 | 高 | 高 | M1: 本地队列模式验证 |

### P2（长期演进，3 个月+）

| # | 不足 | 建议 | 实现代价 | 预期收益 | 关键里程碑 |
|---|------|------|---------|---------|-----------|
| 9 | Skill 路由硬编码 | B. Skill 路由外置 YAML | 低 | 中 | M1: 路由表可运行时加载 |
| 10 | 架构可扩展性差 | T. Worker Type Registry | 低 | 中 | M1: Registry YAML 文件 |
| 11 | 进程池预热缺失 | I. Worker 进程池预热 | 中 | 高 | M1: 预热池容量可配置 |
| 12 | 流式输出缺失 | J. 流式输出捕获 | 高 | 高 | M1: stderr 实时流 |

---

## 六、执行策略建议

### 短期（1-2 周）：止血 + 可观测性

```
优先级: N(可观测性) > W(测试) > A(LMM拆解) > H(事件驱动)
目标: 让系统可测量、可测试、有基准
```

**立即行动项**：
1. 接入 `prometheus_client`，暴露 5-10 个核心 metrics（task_total、worker_running、task_duration 等）
2. 补充 CEO 真实调用测试（取消 mock harness）和 wave 依赖测试
3. 将 LLM 拆解从 fallback 提升为主路径，优化 prompt 质量
4. 实现回调机制替代轮询（关键里程碑：H1）

### 中期（1 个月）：调度能力补课

```
优先级: D(多skill) > K(差异化超时) > F(Context契约) > Q(消息队列本地模式)
目标: 调度能力接近生产级
```

**关键里程碑**：
- 多 skill 候选路由上线（1 个 task 可触发 N 个 skills）
- worker_type 级超时配置外部化
- manifest 契约声明 + 产出校验
- 消息队列本地模式验证（不依赖远程服务器，先验证架构可行性）

### 长期（3 个月+）：分布式架构

```
优先级: Q(远程Worker) > S(PostgreSQL) > R(Docker) > U(Plugin)
目标: 具备水平扩展能力和生态扩展性
```

**关键里程碑**：
- Remote Worker 模式稳定运行
- PostgreSQL 主从复制验证
- K8s 调度 + HPA 自动扩缩容
- Plugin 生态示例和文档

---

## 七、不建议此时投入的方向

以下特性在可观测性和调度能力尚未就绪时，优化收益为零：

- **多租户隔离**：系统还没稳定到需要隔离，先把单租户场景做扎实
- **商业化计费**：计费依赖准确的用量计量，而用量计量依赖可观测性
- **复杂的 DAG 可视化**：在没有可靠 DAG 执行基础时，可视化是空中楼阁
- **AI 自动调参**：基于数据的动态调参需要先有数据积累

---

*报告生成时间：2026-05-12 · 基于 ceo_brain.py、harness.py、worker_pool.py、session_store.py、run.py 源码及 test_ceo_brain.py、test_harness.py、test_performance.py 测试文件分析*

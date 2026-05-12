# MonoSwarm 不足识别与进化路线图

**项目路径**: `/Users/yutanglao/.hermes/agent-swarm/`
**分析日期**: 2026-05-12
**基于**: 源码分析（ceo_brain.py, harness.py, worker_pool.py, session_store.py, api_server.py, config.py, run.py）

---

## 一、任务拆解能力（rule-based vs LLM-based）

### 现状
- CEO Brain 支持两种拆解策略：规则匹配（rule-based）和 LLM 智能拆解
- 规则判断逻辑：检测多意图信号（"和"、"然后"等）和明确类型关键词
- LLM 拆解通过 `hermes chat -q` 调用外部 LLM，JSON 输出后解析

### 不足

| 维度 | 问题 | 代码位置 |
|------|------|----------|
| 规则脆弱性 | `rule_based_decompose` 依赖关键词匹配，误判率高。例如"做PPT"会被错误路由到其他 worker | ceo_brain.py:289-406 |
| 依赖关系静态 | `dependency_rules` 硬编码 `doc_worker→code_worker`，无法表达复杂 DAG | ceo_brain.py:491-495 |
| LLM 降级失败 | LLM 拆解失败时仅打印日志，回退到规则方法，不通知用户 | ceo_brain.py:279-287 |
| 无子任务粒度控制 | 拆解粒度完全取决于 LLM 或规则，无法让用户指定"粗拆/细拆" | ceo_brain.py:165-194 |
| 上下文丢失 | 拆解结果无版本管理，历史拆解决策不可追溯 | session_store.py |

### 进化路线

**短期（1-2周）**
- 增加规则匹配优先级表，明确互斥规则（如"PPT"和"代码开发"同时出现时的处理策略）
- LLM 降级时增加结构化错误信息，让用户知道使用了兜底规则

**中期（1个月）**
- 引入子任务粒度参数：`decompose(task, granularity=1-5)`，控制拆解深度
- 将 `dependency_rules` 从硬编码改为声明式配置，支持 YAML/JSON 定义复杂 DAG
- 增加拆解历史表（`task_decompositions`），记录每次拆解的输入、输出、策略选择

**长期（3个月）**
- 构建拆解策略优化器：基于历史任务数据，自动学习最优拆解模式
- 引入意图分类模型，专门处理"做+类型"歧义场景

---

## 二、调度能力（DAG、超时控制、优先级）

### 现状
- Harness 层通过 `execute_all(worker_ids, parallel)` 支持并行/顺序执行
- Worker 通过 `threading.Thread` + `subprocess.Popen` 启动，监控线程独立运行
- 超时控制：worker 默认 2 小时超时（`communicate(timeout=7200)`）
- 重试机制：`retry_failed()` 支持按 `max_retries` 重试

### 不足

| 维度 | 问题 | 代码位置 |
|------|------|----------|
| 无真实 DAG 调度 | `execute_all` 的 parallel 模式是"全部并发等待"，而非拓扑序执行 | harness.py:160-186 |
| 超时控制粗糙 | 全局 2 小时超时，无法为不同类型 worker 设置不同超时阈值 | worker_pool.py:162 |
| 无优先级机制 | 所有 worker 在 `pending` 队列中 FIFO 处理，无优先级抢占 | worker_pool.py:77-149 |
| 资源隔离缺失 | 并发数由 `max_concurrent` 控制，但无法限制单个 worker 的 CPU/内存使用 | harness.py:45-54 |
| 无 worker 亲和性 | 相同类型的 worker 每次可能调度到不同的 skill，无法保证一致性 | worker_pool.py:36-45 |

### 进化路线

**短期**
- 引入 worker 级超时配置：`spawn(worker_id, max_retries, timeout=3600)` 按 worker 类型设置不同超时
- 增加 `priority` 字段到 worker 创建，支持高优先级 worker 优先调度

**中期**
- 实现拓扑排序调度器：读取 task 依赖关系，按 DAG 顺序执行（依赖完成才调度）
- 引入资源配额（CPU/内存上限），通过 `resource` 模块实现隔离
- 增加 worker 亲和性策略：同类 worker 复用相同 skill 版本，避免版本漂移

**长期**
- 构建分布式的调度层：Harness 支持多实例，通过消息队列（如 Redis）协调
- 引入动态优先级：根据 worker 饥饿时间、任务紧急度动态调整调度顺序

---

## 三、伸缩性（分布式、单机故障、资源隔离）

### 现状
- 所有 worker 在同一进程内通过 `ThreadPoolExecutor` 管理
- worker_pool 是单例（`get_worker_pool()`），全局共享
- 任务持久化在 SQLite（`swarm.db`），单文件存储

### 不足

| 维度 | 问题 | 代码位置 |
|------|------|----------|
| 无分布式支持 | worker 无法跨机器运行，水平扩展受单机资源限制 | worker_pool.py:77 |
| 单点故障 | SQLite 在高并发写入时可能锁表；worker_pool 单例崩溃会导致所有 worker 丢失 | session_store.py:18-23, worker_pool.py:297-307 |
| 无资源隔离 | 所有 worker 共享宿主机的 CPU/内存，大型任务可能压垮系统 | worker_pool.py:122-129 |
| 无 worker 心跳 | 无法感知 worker 存活状态，僵尸进程可能不被发现 | worker_pool.py:151-212 |
| 状态存储简陋 | `swarm.db` 无版本迁移机制，schema 变更需要手动处理 | session_store.py:26-95 |

### 进化路线

**短期**
- 增加 worker 健康检查：spawn 时记录 PID，定期检测进程是否存活
- 引入基础的资源监控：`psutil` 监控 CPU/内存使用，超阈值告警

**中期**
- 迁移到 PostgreSQL：支持高并发写入、主从复制、连接池
- 引入消息队列（Redis/RabbitMQ）：worker 从队列拉取任务，支持分布式部署
- 实现 worker 容器化（Docker）：资源隔离、版本固化、环境一致性

**长期**
- 构建 Kubernetes 原生调度：worker 作为 K8s Job/Pod，支持自动扩缩容
- 引入服务网格（Istio）：流量管理、服务发现、熔断器
- 多区域部署：根据 worker 地理位置就近调度，减少网络延迟

---

## 四、可观测性（实时日志、进度追踪、流式输出）

### 现状
- worker 执行结果通过 `stdout` 捕获，限制 5000 字符（`worker_pool.py:168`）
- 错误信息通过 `stderr` 捕获，限制 2000 字符
- 状态更新通过 `update_worker_status()` 写入 SQLite
- Harness 层聚合结果时截断到 1500 字符（`harness.py:333`）

### 不足

| 维度 | 问题 | 代码位置 |
|------|------|----------|
| 无实时日志 | stdout/stderr 仅在 worker 完成后才能读取，无法实时追踪 | worker_pool.py:162-180 |
| 流式输出缺失 | worker 输出全量返回，无法流式展示长时任务的中间结果 | worker_pool.py:166-169 |
| 日志无分级 | 所有输出都是 "result"，无 INFO/WARN/ERROR 分级 | worker_pool.py:166-180 |
| 进度追踪弱 | 仅通过状态（pending/running/completed）感知进度，无百分比/阶段概念 | worker_pool.py:202-212 |
| 无审计日志 | event_log 表仅记录状态变更，缺少操作审计能力 | session_store.py:68-76 |

### 进化路线

**短期**
- 增加日志等级标记：stdout 通过特定格式（`[INFO]/[WARN]/[ERROR]`）区分日志级别
- 延长输出限制：对 completed 状态的 worker，保留完整输出（不截断 5000 字符）

**中期**
- 引入日志聚合服务：worker 输出实时写入日志文件（如 `/var/log/monoswarm/`），支持 `tail -f` 追踪
- 实现流式输出 API：WebSocket 端点，实时推送 worker 输出片段
- 增加进度协议：worker 定期上报 `{stage: "parsing", progress: 0.45}`，前端展示进度条

**长期**
- 集成 ELK/Grafana Loki：集中式日志存储、查询、可视化
- 引入分布式追踪（OpenTelemetry）：跨 worker 的完整调用链追踪
- 增加 SLA 监控：P50/P95/P99 延迟指标，异常自动告警

---

## 五、开发者体验（API设计、扩展机制、调试能力）

### 现状
- Worker 类型通过 `WORKER_TYPE_SKILLS` 映射表静态配置
- Skill 路由通过 `select_skill_for_task()` 动态匹配
- CEO Brain 通过 `SKILL_ROUTING` 表实现关键词 → skill 的路由
- 调试手段：print 日志 + SQLite 查询状态

### 不足

| 维度 | 问题 | 代码位置 |
|------|------|----------|
| 扩展点单一 | 仅通过 skill 扩展，无 plugin 机制；新增 worker 类型需要修改代码 | worker_pool.py:36-45 |
| API 不一致 | CEO Brain 的 `SKILL_ROUTING` 和 Worker Pool 的 `WORKER_TYPE_SKILLS` 两套映射，容易不一致 | ceo_brain.py:29-86, worker_pool.py:36-45 |
| 调试困难 | worker 跑在子进程中，无法 attach 调试；错误仅通过 stderr 返回 | worker_pool.py:122-129 |
| 无模拟/Mock 机制 | 无法在开发环境模拟外部依赖（API/数据库） | - |
| skill 版本失控 | 同一 worker 类型可能每次加载不同版本的 skill，无版本锁定 | ceo_brain.py:101-122 |

### 进化路线

**短期**
- 统一路由配置：合并 `SKILL_ROUTING` 和 `WORKER_TYPE_SKILLS` 为单一配置源（YAML）
- 增加 worker 调试模式：`DEBUG=1` 时 worker 保留完整环境信息（PID、启动参数、日志路径）
- 提供 `hermes swarm dry-run` 命令：模拟执行但不真正启动 worker

**中期**
- 引入 Plugin 系统：动态加载 `~/.hermes/monoswarm/plugins/` 目录下的 worker 类型
- 实现 skill 版本锁定：`skill_lock.json` 记录每个 worker 类型对应的 skill 版本
- 增加 Web UI：可视化任务状态、worker 日志、依赖关系图

**长期**
- 构建 Marketplace：分享/发现自定义 worker 类型和 skill 组合
- 引入完整的 SDK：Python/JS/Go 多语言 SDK，支持用户自建 worker 类型
- 实现断点调试：通过 DAP（Debug Adapter Protocol）支持 VSCode/IDEA 远程调试 worker

---

## 六、运维能力（高可用、多租户、监控告警）

### 现状
- 无高可用设计：Harness/WorkerPool 单实例，崩溃后无自动恢复
- 无多租户支持：所有任务共享同一 worker 池，无法隔离不同用户/项目的资源
- 监控告警：无监控面板、无告警渠道、无 SLA 指标

### 不足

| 维度 | 问题 | 代码位置 |
|------|------|----------|
| 无高可用 | Harness 单点故障，无主备切换或自动重启机制 | harness.py:33-54 |
| 多租户缺失 | worker 池无命名空间/标签隔离，"租户A"的任务可能调度到"租户B"的 worker | worker_pool.py:77-149 |
| 无资源配额 | 无法为不同用户/任务设置 CPU/内存/并发数上限 | harness.py:45 |
| 监控空白 | 无 metrics 收集、无 dashboards、无告警规则 | - |
| 升级困难 | 代码更新需要重启服务，期间任务可能中断 | run.py |

### 进化路线

**短期**
- 增加进程管理：使用 `supervisor` 或 `systemd` 管理 Harness/WorkerPool，提供自动重启
- 引入基础监控：`prometheus_client` 暴露核心指标（任务数、worker 状态、执行时长）
- 简单的多租户标签：在 worker/task 表增加 `tenant_id` 字段，查询时自动过滤

**中期**
- 实现热升级：Graceful shutdown + 任务迁移，支持零停机更新
- 完整的资源配额系统：基于 `tenant_id` 的 CPU/内存/并发数配额控制
- 告警集成：对接 PagerDuty/OpsGenie，支持邮件/Slack/飞书通知

**长期**
- 容器化部署：Docker Compose / K8s，提供健康检查、自动扩缩容
- 多区域部署：跨 AZ/跨region 部署最近可用的 worker 池
- 商业化多租户：完整租户隔离、计费、审计日志

---

## 七、总结：优先改进矩阵

| 维度 | 短期（1-2周） | 中期（1个月） | 长期（3个月+） |
|------|---------------|---------------|----------------|
| 任务拆解 | 规则优先级表、LLM降级错误提示 | 粒度控制、DAG声明式配置 | 拆解策略优化器 |
| 调度能力 | worker级超时、优先级字段 | 拓扑排序调度器、资源配额 | 分布式调度层 |
| 伸缩性 | 健康检查、资源监控 | PostgreSQL迁移、消息队列 | K8s原生部署 |
| 可观测性 | 日志分级、输出不截断 | 流式输出、进度协议 | ELK集成、分布式追踪 |
| 开发者体验 | 统一路由配置、调试模式 | Plugin系统、skill版本锁定 | SDK多语言支持、Marketplace |
| 运维能力 | 进程管理、基础监控 | 热升级、资源配额 | K8s部署、多区域 |

**关键路径建议**：
1. **可观测性优先** — 没有日志/监控，其他优化都是盲人摸象
2. **调度能力其次** — DAG 和超时控制是生产级任务调度的基石
3. **伸缩性第三** — 单机瓶颈不解决，高可用和多租户无从谈起

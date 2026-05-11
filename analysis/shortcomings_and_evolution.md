# MonoSwarm 不足识别与演进路径

**分析日期**: 2026-05-12  
**基于**: `current_state.md` + 源码审计（ceo_brain.py / harness.py / session_store.py / worker_pool.py）

---

## ① CEO 规则覆盖不完整（关键词漏匹配）

**严重程度**: 高

**问题描述**:  
`ceo_brain.py` 第 91–174 行 `_rule_based_decompose()` 依赖纯字符串小写匹配，关键词覆盖极为有限。

典型漏匹配场景：

| 用户任务 | 期望 Worker | 实际结果 |
|---------|------------|---------|
| "帮我调试这个API为什么报错" | code_worker | generic_worker（"调试"未收录） |
| "调研一下竞品的功能差异" | research_worker | generic_worker（"调研"未收录） |
| "帮我优化下这个SQL查询性能" | code_worker | generic_worker（"优化"/"SQL"未收录） |
| "做一个用户增长的数据分析" | research_worker/doc_worker | generic_worker（"分析"/"增长"未收录） |
| "把这份PDF转成Word" | doc_worker | generic_worker（"转"/"Word"未收录） |

关键词词库规模估算：约 40 个词，覆盖面不到 10%。

**演进路径**:

- **短期**（1–2 周）: 人工扩充关键词表，覆盖常见动词（调试/调研/优化/分析/转换）和名词（SQL/API/竞品/增长），增加同义词映射。快速止血，无需架构改动。
- **中期**（1 个月）: 引入轻量语义匹配（依赖 `sklearn` 或 `spacy` 的 TF-IDF / cosine similarity），脱离硬编码关键词，支持模糊匹配。
- **长期**（2–3 个月）: 接入 LLM 做智能拆解（`gpt-4o-mini` 或本地模型），输入任务描述 + WORKER_TYPES 描述，让 LLM 输出子任务列表。代码已预留 TODO，替换 `_rule_based_decompose` 为 `_llm_decompose` 即可。

---

## ② SQLite 未开启 foreign_keys

**严重程度**: 中

**问题描述**:  
`session_store.py` 第 31–87 行建表时声明了 `FOREIGN KEY (task_id) REFERENCES tasks(id)` 和 `FOREIGN KEY (worker_id) REFERENCES workers(id)`，但 `get_db()` 函数（第 18–23 行）从未执行 `PRAGMA foreign_keys = ON`。

后果：

- 级联删除无效（删除 task 时 workers 记录残留）
- 插入时可以引用不存在的 task_id / worker_id（脏数据风险）
- `ceo_assignments` 的双外键无任何约束

**演进路径**:

- **短期**（1 天）: 在 `get_db()` 中 `conn.execute("PRAGMA foreign_keys = ON")` 后再返回。零成本，立即生效。
- **中期**: 在 `init_db()` 开头执行 `PRAGMA foreign_keys = ON`，并在每次 `get_db()` 时校验连接是否已开启。
- **长期**: 考虑迁移到 PostgreSQL（如果任务规模扩大），SQLite 适合轻量单进程，生产级多实例需更强约束。

---

## ③ subprocess shell=True 潜在注入风险

**严重程度**: 中

**问题描述**:  
`worker_pool.py` 第 93 行：

```python
cmd = f"hermes chat {skill_flag} -q {json.dumps(full_goal)} --quiet"
proc = subprocess.Popen(cmd, shell=True, stdout=PIPE, stderr=PIPE, text=True, cwd=PROJECT_ROOT)
```

`full_goal` 内容来自用户任务描述，经 `json.dumps` 转义后通过 `shell=True` 执行。若 `full_goal` 中存在未转义的特殊 shell 字符（`|`, `&`, `$`, `` ` ``, `;`），可能造成命令注入。

虽然当前场景下 `full_goal` 来自可信的内部 LLM 输出，但仍属于防御性编码禁区。

**演进路径**:

- **短期**（1 天）: 将 `shell=True` 改为 `shell=False`，以 list 形式传参：

  ```python
  args = ["hermes", "chat"] + (["-s", skill] if skill else []) + ["-q", full_goal, "--quiet"]
  proc = subprocess.Popen(args, stdout=PIPE, stderr=PIPE, text=True, cwd=PROJECT_ROOT)
  ```

  完全消除 shell 注入风险。

- **中期**: 对 `full_goal` 做白名单过滤（移除控制字符），即便未来误用 `shell=True` 也有保障。

---

## ④ 代码 305 行偏长

**严重程度**: 低

**问题描述**:  
`ceo_brain.py` 总计 305 行，`WORKER_TYPES` 定义（27 行） + `_rule_based_decompose`（83 行） + 其他逻辑混在一起，单文件职责不清晰。

更严重的是，`run_full_flow()`（第 248–286 行）把拆解/执行/汇总三件大事一次性做完，违反单一职责原则。

**演进路径**:

- **短期**: 将 `WORKER_TYPES` 抽取到独立文件 `worker_types.py`，关键词表抽取到 `keywords.py` 或 YAML 配置。
- **中期**: 分离 `run_full_flow` 为三个独立方法，CEOBrain 只负责拆解，Harness 负责执行，Aggregator 负责汇总。符合管道式架构。
- **长期**: 按领域驱动设计（DDD）重组为 `domain/`, `application/`, `infrastructure/` 目录结构。

---

## ⑤ 无 LLM 驱动的任务拆解

**严重程度**: 高

**问题描述**:  
这是①的根因。代码第 87–88 行明确写了：

```python
# 基于规则的简单任务拆解（后续可替换为LLM智能拆解）
decomposition = self._rule_based_decompose(task_description)
```

且 `_rule_based_decompose` 内部（第 94 行）也有 TODO 注释：

```python
# TODO: 后续替换为LLM智能拆解
```

目前纯规则匹配无法处理：
- 隐含子任务（用户说"做个PPT"但没提内容，需要自动拆出"内容规划"）
- 多跳依赖（"先查数据，再做分析，最后画图"——三个阶段有依赖关系）
- 任务优先级排序

**演进路径**:

- **短期**: 保留规则引擎作为兜底，上层包装 LLM 拆解尝试，失败时回退到规则引擎。
- **中期**: 直接用 LLM 拆解，配合 Few-shot examples 提升质量。Worker 类型描述（`WORKER_TYPES`）直接作为 LLM 的 system prompt。
- **长期**: 构建任务拆解的评估数据集，持续优化拆解质量（有反馈回路）。

---

## ⑥ 缺乏可观测性 / 监控

**严重程度**: 中

**问题描述**:  
当前系统没有任何监控机制：

- 无任务执行时长追踪
- 无 Worker 失败率统计
- 无重试成功率指标
- 无资源使用监控（CPU/内存）
- `event_log` 仅记录状态变更，无结构化指标

故障只能靠人工登录查日志（`swarm.db` 或 print 输出）。

**演进路径**:

- **短期**（1 周）: 在 `session_store.py` 增加 `task_metrics` 表，记录每个 task/worker 的执行时长、内存峰值、重试次数。在 `harness.py` 的关键节点（dispatch / execute_all / retry）打时间戳。
- **中期**: 接入轻量指标库（如 `prometheus_client`），暴露 `swarm_tasks_total`、`swarm_workers_running`、`swarm_task_duration_seconds` 等指标。可选接入 Grafana 可视化。
- **长期**: 接入分布式追踪（OpenTelemetry），追踪一个任务在多个 Worker 间的完整生命周期。接入告警（PagerDuty / 飞书机器人），当失败率超过阈值时自动通知。

---

## 汇总表

| # | 不足 | 严重程度 | 短期（1–2周） | 中期（1个月） | 长期（2–3个月） |
|---|------|---------|--------------|--------------|----------------|
| ① | CEO 规则覆盖不完整 | 高 | 扩充关键词表 | TF-IDF 语义匹配 | LLM 智能拆解 |
| ② | SQLite foreign_keys=OFF | 中 | 1行 PRAGMA 修复 | 连接层默认开启 | 评估 PostgreSQL |
| ③ | subprocess shell=True | 中 | 改为 list 传参 | 白名单过滤 | 审计与沙箱化 |
| ④ | 代码 305 行偏长 | 低 | 抽离 WORKER_TYPES | 分离 CEO/Harness/Aggregator | DDD 目录重组 |
| ⑤ | 无 LLM 驱动的拆解 | 高 | LLM 兜底 + 回退 | 直接 LLM 拆解 | 评估数据集 + 迭代 |
| ⑥ | 无可观测性/监控 | 中 | 增加 metrics 表 | Prometheus 接入 | OpenTelemetry + 告警 |

---

*本文档为静态分析，未实际运行测试。如需验证，建议补充运行时 tracing 或实际压测数据。*

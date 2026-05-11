# MonoSwarm

Agent Swarm System - 无状态Agent蜂群调度层

## 架构

```
ceo_brain.py      # CEO Brain - 任务拆解 + 结果汇总
harness.py        # Harness - 调度层核心
worker_pool.py    # Worker Pool - Worker生命周期管理
session_store.py  # Session Store - SQLite持久化层
config.py         # 配置
run.py            # CLI入口
```

## 快速开始

### 安装

```bash
./deploy.sh install
```

### 使用

```bash
# 执行任务
python3 ~/.hermes/monoswarm/run.py "写一个简单的Python计算器程序"

# 查看任务状态
python3 ~/.hermes/monoswarm/run.py status <task_id>

# 查看所有任务
python3 ~/.hermes/monoswarm/run.py tasks

# 查看任务日志
python3 ~/.hermes/monoswarm/run.py log <task_id>

# 运行测试
python3 ~/.hermes/monoswarm/run.py test
```

## Worker 类型

| 类型 | 描述 |
|------|------|
| code_worker | 代码开发 |
| ppt_worker | PPT制作 |
| video_worker | 视频制作 |
| ui_worker | UI设计 |
| qa_worker | 测试验证 |
| doc_worker | 文档处理 |
| research_worker | 调研搜索 |
| generic_worker | 通用任务 |

## 依赖

仅使用Python标准库，无需额外安装依赖。

需要预先安装 Hermes Agent CLI（用于Worker执行）。

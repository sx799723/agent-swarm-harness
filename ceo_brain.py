#!/usr/bin/env python3
"""
Agent Swarm CEO Brain
CEO 任务拆解 + 结果汇总

负责：
1. 理解用户任务
2. 拆解成可执行的子任务（Worker Goals）
3. 把任务交给 Harness 执行
4. 汇总 Worker 结果形成最终报告
"""

import json
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from harness import AgentSwarmHarness


# ─────────────────────────────────────────
# Worker 类型定义
# ─────────────────────────────────────────

WORKER_TYPES = {
    "code_worker": {
        "description": "执行编码任务（前端/后端/脚本/数据库等）",
        "skills": ["software-development/skill-creator"],
    },
    "ppt_worker": {
        "description": "执行PPT/演示文稿制作",
        "skills": ["productivity/ppt-workflow"],
    },
    "video_worker": {
        "description": "执行视频剪辑、配音、导出",
        "skills": ["media/youtube-content", "media/gif-search"],
    },
    "ui_worker": {
        "description": "执行UI设计、图标、海报",
        "skills": ["creative/baoyu-comic", "creative/baoyu-cover-image"],
    },
    "qa_worker": {
        "description": "执行测试、验证、质量检查",
        "skills": ["software-development/test-driven-development"],
    },
    "doc_worker": {
        "description": "执行文档处理、数据整理、表格",
        "skills": ["productivity/spreadsheet", "productivity/ocr-and-documents"],
    },
    "research_worker": {
        "description": "执行调研、搜索、信息整理",
        "skills": ["research/arxiv", "research/blogwatcher"],
    },
    "generic_worker": {
        "description": "通用任务执行",
        "skills": [],
    },
}


# ─────────────────────────────────────────
# CEO Brain 提示词
# ─────────────────────────────────────────

TASK_DECOMPOSITION_PROMPT = """你是一个任务拆解专家。当用户给出一个任务时，你需要：

1. **理解任务意图**：用户真正想要的是什么？
2. **拆解子任务**：把大任务拆成3-8个可并行执行的子任务
3. **匹配Worker类型**：每个子任务匹配一个最合适的Worker类型
4. **输出结构化JSON**

## Worker类型说明：
{worker_types}

## 输出格式（必须严格JSON）：
```json
{{
  "task_title": "任务标题",
  "task_description": "任务详细描述",
  "task_goal": "CEO给Harness的总目标描述",
  "subtasks": [
    {{
      "id": "subtask-1",
      "title": "子任务标题",
      "goal": "具体的执行goal，要清晰、可操作",
      "worker_type": "code_worker|ppt_worker|video_worker|ui_worker|qa_worker|doc_worker|research_worker|generic_worker",
      "context": {{}},
      "depends_on": []
    }}
  ]
}}
```

## 规则：
- subtasks 数量控制在 3-8 个
- goal 描述要具体，包含交付标准
- depends_on 填写依赖的 subtask id（可选）
- context 填写任务需要的额外信息（如文件路径、参考资料等）
- 如果任务无法拆分（如只是简单问答），subtasks 可以只有1个
"""


RESULT_AGGREGATION_PROMPT = """你是一个结果汇总专家。Worker们已经完成了各自的任务，现在需要你：

1. **分析每个Worker的结果**：哪些成功了，哪些失败了
2. **识别关键成果**：用户最关心的交付物是什么
3. **识别问题**：哪里出了问题，原因是什么
4. **给出建议**：下一步应该怎么做

## Worker执行结果：
{worker_results}

## 原始任务目标：
{task_goal}

## 输出格式：
```
## 执行汇总

### 总体状态
- 成功: X/Y
- 失败: X/Y

### 关键成果
[列出最重要的交付物]

### 问题分析
[列出失败原因和改进建议]

### 下一步建议
[告诉用户接下来应该做什么]
```

请给出清晰、可操作的汇总报告。
"""


# ─────────────────────────────────────────
# CEO Brain 类
# ─────────────────────────────────────────

class CEOBrain:
    """
    CEO Brain — 任务拆解 + 结果汇总

    使用方式：
    ceo = CEOBrain()
    workers = ceo.decompose(task_description)  # 拆解任务
    harness.run(task_id, workers)               # 执行
    report = ceo.aggregate(task_id, results)   # 汇总
    """

    def __init__(self, model: str = None):
        self.model = model  # 后续可扩展用指定模型
        self.harness = AgentSwarmHarness()

    def decompose(self, task_description: str) -> dict:
        """
        理解任务并拆解成子任务

        Returns: dict with task info + subtasks
        """
        # 格式化 worker 类型说明
        worker_types_str = "\n".join([
            f"- {k}: {v['description']}"
            for k, v in WORKER_TYPES.items()
        ])

        prompt = TASK_DECOMPOSITION_PROMPT.format(
            worker_types=worker_types_str
        )

        # 这里调用LLM进行拆解（当前版本先用规则）
        # TODO: 后续接入LLM进行真正的智能拆解
        import uuid

        # 简单规则拆解（演示用）
        # 后续替换为LLM调用
        decomposition = self._rule_based_decompose(task_description)

        return decomposition

    def _rule_based_decompose(self, task_description: str) -> dict:
        """
        基于规则的简单任务拆解
        TODO: 后续替换为LLM智能拆解
        """
        import uuid
        import time

        task_id = f"task-{uuid.uuid4().hex[:8]}"
        task_lower = task_description.lower()

        subtasks = []

        # 代码相关任务
        if any(kw in task_lower for kw in ["写", "开发", "代码", "api", "前端", "后端", "脚本", "程序"]):
            subtasks.append({
                "id": f"subtask-{uuid.uuid4().hex[:6]}",
                "title": "代码开发",
                "goal": task_description,
                "worker_type": "code_worker",
                "context": {},
            })

        # PPT相关
        if any(kw in task_lower for kw in ["ppt", "演示", "幻灯片", "presentation"]):
            subtasks.append({
                "id": f"subtask-{uuid.uuid4().hex[:6]}",
                "title": "PPT制作",
                "goal": task_description,
                "worker_type": "ppt_worker",
                "context": {},
            })

        # 视频相关
        if any(kw in task_lower for kw in ["视频", "剪辑", "movie", "video"]):
            subtasks.append({
                "id": f"subtask-{uuid.uuid4().hex[:6]}",
                "title": "视频制作",
                "goal": task_description,
                "worker_type": "video_worker",
                "context": {},
            })

        # UI/设计相关
        if any(kw in task_lower for kw in ["设计", "ui", "图", "海报", "icon", "logo"]):
            subtasks.append({
                "id": f"subtask-{uuid.uuid4().hex[:6]}",
                "title": "UI设计",
                "goal": task_description,
                "worker_type": "ui_worker",
                "context": {},
            })

        # 测试相关
        if any(kw in task_lower for kw in ["测试", "test", "验证"]):
            subtasks.append({
                "id": f"subtask-{uuid.uuid4().hex[:6]}",
                "title": "测试验证",
                "goal": task_description,
                "worker_type": "qa_worker",
                "context": {},
            })

        # 文档相关
        if any(kw in task_lower for kw in ["文档", "报告", "表格", "doc", "excel", "csv"]):
            subtasks.append({
                "id": f"subtask-{uuid.uuid4().hex[:6]}",
                "title": "文档处理",
                "goal": task_description,
                "worker_type": "doc_worker",
                "context": {},
            })

        # 默认通用任务
        if not subtasks:
            subtasks.append({
                "id": f"subtask-{uuid.uuid4().hex[:6]}",
                "title": "通用任务",
                "goal": task_description,
                "worker_type": "generic_worker",
                "context": {},
            })

        return {
            "task_title": task_description[:50],
            "task_description": task_description,
            "task_goal": f"完成用户任务：{task_description}",
            "subtasks": subtasks,
        }

    def execute(self, decomposition: dict, parallel: bool = True) -> dict:
        """
        执行拆解后的任务

        Args:
            decomposition: decompose() 返回的结果
            parallel: 是否并行执行

        Returns:
            执行结果（包含 harness.run 的完整返回）
        """
        task_description = decomposition["task_description"]
        subtasks = decomposition["subtasks"]

        # 1. 创建任务
        task_id = self.harness.create_task(
            title=decomposition["task_title"],
            description=decomposition["task_description"],
            goal=decomposition["task_goal"],
        )

        # 2. 转换为 harness 格式
        workers = [
            {
                "id": st["id"],
                "worker_type": st["worker_type"],
                "goal": st["goal"],
                "context": st.get("context", {}),
            }
            for st in subtasks
        ]

        # 3. 一站式执行
        result = self.harness.run(task_id, workers, parallel=parallel)

        return result

    def aggregate(self, task_id: str, task_goal: str, worker_results: dict) -> str:
        """
        汇总 Worker 结果形成最终报告
        """
        # 格式化 worker 结果
        results_str = json.dumps(worker_results, ensure_ascii=False, indent=2)

        prompt = RESULT_AGGREGATION_PROMPT.format(
            worker_results=results_str,
            task_goal=task_goal,
        )

        # 当前版本：简单文本汇总
        # TODO: 后续接入LLM进行智能汇总
        report_lines = ["## 任务执行报告\n"]
        report_lines.append(f"**任务**: {task_goal}\n")

        success_count = sum(1 for r in worker_results.values() if r.get("status") == "completed")
        total_count = len(worker_results)

        report_lines.append(f"**状态**: {success_count}/{total_count} Worker成功\n")

        for wid, result in worker_results.items():
            icon = "✅" if result.get("status") == "completed" else "❌"
            report_lines.append(f"\n### {icon} {wid}")
            if result.get("result"):
                res = result["result"]
                preview = res[:500] + "..." if len(res) > 500 else res
                report_lines.append(f"```\n{preview}\n```")
            if result.get("error"):
                report_lines.append(f"**错误**: {result['error']}")

        return "\n".join(report_lines)

    def run_full_flow(self, task_description: str, parallel: bool = True) -> dict:
        """
        完整流程：拆解 → 执行 → 汇总

        Returns:
            {
                "task_id": ...,
                "decomposition": ...,
                "execution": ...,
                "report": ...
            }
        """
        print(f"[CEO] 收到任务: {task_description}")

        # 1. 拆解
        print("[CEO] 拆解任务中...")
        decomposition = self.decompose(task_description)
        print(f"[CEO] 拆解为 {len(decomposition['subtasks'])} 个子任务")
        for st in decomposition["subtasks"]:
            print(f"  - {st['worker_type']}: {st['goal'][:60]}...")

        # 2. 执行
        print("[CEO] 分发执行中...")
        execution = self.execute(decomposition, parallel=parallel)
        print(f"[CEO] 执行完成: {execution['status']}")

        # 3. 汇总
        print("[CEO] 汇总结果中...")
        report = self.aggregate(
            task_id=execution["task_id"],
            task_goal=decomposition["task_goal"],
            worker_results=execution["worker_results"],
        )

        return {
            "task_id": execution["task_id"],
            "decomposition": decomposition,
            "execution": execution,
            "report": report,
        }


# ─────────────────────────────────────────
# CLI 入口
# ─────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Agent Swarm CEO Brain")
    parser.add_argument("task", help="要执行的任务描述")
    parser.add_argument("--serial", action="store_true", help="顺序执行而非并行")

    args = parser.parse_args()

    ceo = CEOBrain()
    result = ceo.run_full_flow(args.task, parallel=not args.serial)

    print("\n" + "="*60)
    print(result["report"])

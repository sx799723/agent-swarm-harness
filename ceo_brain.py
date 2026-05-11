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
import uuid

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
        self.model = model
        self.harness = AgentSwarmHarness()

    def decompose(self, task_description: str) -> dict:
        """
        理解任务并拆解成子任务
        Returns: dict with task info + subtasks
        """
        # 基于规则的简单任务拆解（后续可替换为LLM智能拆解）
        decomposition = self._rule_based_decompose(task_description)
        return decomposition

    def _rule_based_decompose(self, task_description: str) -> dict:
        """
        基于规则的简单任务拆解
        TODO: 后续替换为LLM智能拆解
        """
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

    def _make_worker_goal(self, goal: str) -> str:
        """
        给 Worker 的 goal 加上执行指令前缀
        要求 Worker 实际执行任务，而不是仅返回文本
        """
        return (
            "你是一个Worker。你的职责是实际完成任务，而不是仅返回文本描述。\n\n"
            "要求：\n"
            "1. 如果需要写文件，使用 write_file 工具实际写入\n"
            "2. 如果需要运行代码，使用 terminal 工具实际执行\n"
            "3. 完成后返回执行结果（实际输出/文件路径）\n\n"
            "任务：\n" + goal
        )

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

        # 2. 转换为 harness 格式（给每个 goal 加上执行指令前缀）
        workers = [
            {
                "id": st["id"],
                "worker_type": st["worker_type"],
                "goal": self._make_worker_goal(st["goal"]),
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

    print("\n" + "=" * 60)
    print(result["report"])

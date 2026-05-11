#!/usr/bin/env python3
"""
Agent Swarm CEO Brain
CEO 任务拆解 + 结果汇总 + Context Passing（Worker结果复用）

负责：
1. 理解用户任务
2. 拆解成可执行的子任务（Worker Goals）
3. 自动建立 Worker 之间的依赖关系（Context Passing）
4. 把任务交给 Harness 执行
5. 汇总 Worker 结果形成最终报告
"""

import json
import sys
import os
import uuid

sys.path.insert(0, os.path.dirname(__file__))
from config import PROJECT_ROOT
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
        decomposition = self._rule_based_decompose(task_description)
        return decomposition

    def _rule_based_decompose(self, task_description: str) -> dict:
        """
        基于规则的智能任务拆解
        核心原则：每个worker只拿自己需要的那部分任务，不是复制完整任务
        """
        task_lower = task_description.lower()
        subtasks = []

        # ─── 第一步：识别所有需要的维度 ───
        # 优先级原则：具体任务类型（ppt/video/文档）优先于通用关键词（做/写）

        # 具体类型优先判断（优先精确匹配，避免被"做""写"误触发）
        needs_ppt      = any(kw in task_lower for kw in ["ppt", "演示", "幻灯片", "presentation"])
        needs_video    = any(kw in task_lower for kw in ["视频", "剪辑", "movie"])
        needs_ui       = any(kw in task_lower for kw in ["设计", "ui", "海报", "icon", "logo", "视觉"])
        needs_research = any(kw in task_lower for kw in ["调研", "调查", "研究", "搜索", "搜集", "市场"])

        # QA相关：任何"测试/验证/评估/检查/审查"都是qa
        needs_qa       = any(kw in task_lower for kw in ["测试", "验证", "质量评估", "评审", "检查", "审查", "评估"])

        # 文档相关：写/整理 报告、表格、文档
        needs_doc      = any(kw in task_lower for kw in ["文档", "报告", "表格", "excel", "csv", "季度", "年度"])
        needs_doc      = needs_doc or ("整理" in task_lower and any(kw in task_lower for kw in ["文档", "报告", "表格", "数据"]))

        # 代码开发：只有明确说"写代码/开发/实现/脚本/程序"才是开发任务
        # 排除"做PPT/做视频/做设计"这类"做+具体类型"的任务（但做文档不算排除）
        #
        # 区分"开发X，然后做Y"（顺序任务，两者都要）vs "做Y"（只有Y）
        # 策略：如果有明确的代码开发动词（开发/实现/写代码），即使出现"做PPT"，也保留code
        code_dev_verbs = ["写代码", "开发代码", "实现代码", "写个程序", "开发程序", "写个脚本", "写个小工具", "写一个脚本", "开发一个", "开发个", "实现一个", "实现个"]
        has_code_dev_verb = any(kw in task_lower for kw in code_dev_verbs)
        # 只有当任务没有明确代码开发动词，且出现"做+具体类型"时，才排除开发
        code_exclude = (needs_ppt or needs_video or needs_ui) and not has_code_dev_verb
        needs_code = has_code_dev_verb and not code_exclude
        # 兜底："做一个Python/JS/网页"类任务，即使没有明确说"写代码"也算开发
        if not needs_code and not code_exclude:
            needs_code = any(kw in task_lower for kw in ["python", "javascript", "java", "c++", "golang", "rust", "网页", "网站", "前端", "后端", "api", "数据库", "爬虫", "自动化"])

        # ─── 第二步：只加需要的worker，每个有专注goal ───
        if needs_code:
            subtasks.append({
                "id": f"subtask-{uuid.uuid4().hex[:6]}",
                "title": "代码开发",
                "goal": f"在{PROJECT_ROOT}下，【开发/实现】：{task_description}",
                "worker_type": "code_worker",
                "context": {"aspect": "development"},
            })

        if needs_qa:
            subtasks.append({
                "id": f"subtask-{uuid.uuid4().hex[:6]}",
                "title": "测试验证",
                "goal": f"在{PROJECT_ROOT}下，【测试/质量】：{task_description}",
                "worker_type": "qa_worker",
                "context": {"aspect": "testing"},
            })

        if needs_research:
            subtasks.append({
                "id": f"subtask-{uuid.uuid4().hex[:6]}",
                "title": "市场调研",
                "goal": f"【调研/分析】：{task_description}",
                "worker_type": "research_worker",
                "context": {"aspect": "research"},
            })

        if needs_ppt:
            subtasks.append({
                "id": f"subtask-{uuid.uuid4().hex[:6]}",
                "title": "PPT制作",
                "goal": f"【PPT/演示】：{task_description}",
                "worker_type": "ppt_worker",
                "context": {"aspect": "presentation"},
            })

        if needs_ui:
            subtasks.append({
                "id": f"subtask-{uuid.uuid4().hex[:6]}",
                "title": "UI设计",
                "goal": f"【设计/视觉】：{task_description}",
                "worker_type": "ui_worker",
                "context": {"aspect": "design"},
            })

        if needs_video:
            subtasks.append({
                "id": f"subtask-{uuid.uuid4().hex[:6]}",
                "title": "视频制作",
                "goal": f"【视频/剪辑】：{task_description}",
                "worker_type": "video_worker",
                "context": {"aspect": "production"},
            })

        if needs_doc:
            subtasks.append({
                "id": f"subtask-{uuid.uuid4().hex[:6]}",
                "title": "文档处理",
                "goal": f"【文档/报告】：{task_description}",
                "worker_type": "doc_worker",
                "context": {"aspect": "documentation"},
            })

        # 默认：没有任何类型匹配时，用通用worker
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

    def _make_worker_goal(self, goal: str, context: dict = None) -> str:
        """
        给 Worker 的 goal 加上执行指令前缀 + Context Passing 指令
        要求 Worker 实际执行任务，并告知上下游产出位置
        """
        lines = [
            "你是一个Worker。你的职责是实际完成任务，而不是仅返回文本描述。\n",
        ]

        if context:
            lines.append(f"当前任务ID: {context.get('task_id', 'N/A')}")
            lines.append(f"你的工作目录: {context.get('output_dir', PROJECT_ROOT)}")
            lines.append("")

            # 上游依赖信息
            upstream = context.get("input_from", [])
            if upstream:
                lines.append("【重要：上游Worker产出】")
                for up in upstream:
                    lines.append(f"  Worker [{up['worker_id']}] ({up['worker_type']}) 的产出目录: {up['output_dir']}")
                lines.append("请在上述目录中查看上游Worker的产出文件，作为你的输入参考。")
                lines.append("")

            # 下游产出约定
            lines.append("【你的产出】")
            lines.append(f"  产出目录: {context.get('output_dir', PROJECT_ROOT)}")
            lines.append("  请将你的产出文件写入该目录，便于下游Worker读取。")
            lines.append("")

        lines.extend([
            "要求：",
            "1. 如果需要写文件，使用 write_file 工具实际写入",
            "2. 如果需要运行代码，使用 terminal 工具实际执行",
            "3. 完成后返回执行结果（实际输出/文件路径）\n",
            "任务：\n" + goal,
        ])

        return "\n".join(lines)

    def execute(self, decomposition: dict, parallel: bool = True) -> dict:
        """
        执行拆解后的任务

        Context Passing 机制：
        - 每个 Worker 在独立工作目录 workspace/{task_id}/{worker_id}/ 执行
        - 产出自动存储到 output_dir/，供下游 Worker 读取
        - context 包含 input_from 字段，告知 Worker 从哪里读取上游产出
        """
        task_description = decomposition["task_description"]
        subtasks = decomposition["subtasks"]

        # 1. 创建任务
        task_id = self.harness.create_task(
            title=decomposition["task_title"],
            description=decomposition["task_description"],
            goal=decomposition["task_goal"],
        )

        # 2. 构建 Context Passing 信息
        #    建立依赖图：哪些 Worker 的产出是哪些 Worker 的输入
        workspace = os.path.join(PROJECT_ROOT, "workspace", task_id)
        os.makedirs(workspace, exist_ok=True)

        # 根据 worker 类型确定依赖关系
        # doc/qa 依赖 code（代码产出是数据来源）
        # ppt 依赖 doc/qa（报告是 PPT 内容来源）
        dependency_rules = {
            "doc_worker":  ["code_worker"],    # 文档依赖代码产出
            "qa_worker":   ["code_worker"],    # 测试依赖代码产出
            "ppt_worker":  ["doc_worker", "code_worker"],  # PPT 依赖文档/代码
        }

        # 3. 为每个 subtask 构造完整的 context（含 Context Passing 信息）
        workers = []
        for st in subtasks:
            worker_id = st["id"]
            worker_type = st["worker_type"]
            output_dir = os.path.join(workspace, worker_id)
            os.makedirs(output_dir, exist_ok=True)

            # 查找上游 Worker（依赖者）
            deps = dependency_rules.get(worker_type, [])
            upstream_workers = []
            for dep_type in deps:
                for st2 in subtasks:
                    if st2["worker_type"] == dep_type:
                        upstream_workers.append({
                            "worker_id": st2["id"],
                            "worker_type": dep_type,
                            "output_dir": os.path.join(workspace, st2["id"]),
                        })

            # 构造 enhanced context
            context = st.get("context", {}).copy()
            context.update({
                "task_id": task_id,
                "workspace": workspace,
                "output_dir": output_dir,
                "input_from": upstream_workers,  # 上游 Worker 的输出目录列表
                "worker_type": worker_type,
                "subtask_title": st.get("title", ""),
            })

            workers.append({
                "id": worker_id,
                "worker_type": worker_type,
                "goal": self._make_worker_goal(st["goal"], context),
                "context": context,
            })

        # 4. 分 wave 执行（Hand Passing 核心）
        #    先分析依赖图，建立执行层级
        waves = self._build_execution_waves(workers, dependency_rules)

        all_results = {}
        for wave_idx, wave_workers in enumerate(waves):
            wave_num = wave_idx + 1
            print(f"[CEO] 执行 Wave {wave_num}，{len(wave_workers)} 个 Worker 并行...")

            if wave_idx > 0:
                # Hand Passing：把上游产出注入下游 Worker 的 goal
                wave_workers = self._inject_upstream_results(wave_workers, all_results)

            wave_result = self.harness.run(
                task_id,
                wave_workers,
                parallel=True,  # 同 wave 内并行
            )
            all_results.update(wave_result.get("worker_results", {}))

            # 如果有任何 worker 失败，可以选择提前终止
            failed = [wid for wid, r in wave_result.get("worker_results", {}).items()
                      if r.get("status") != "completed"]
            if failed:
                print(f"[CEO] Wave {wave_num} 中 {len(failed)} 个 Worker 失败，继续执行...")

        # 5. 汇总结果
        aggregated = self.harness.aggregate_results({
            wid: self._dict_to_result(r) for wid, r in all_results.items()
        })
        self.harness.update_task_status(task_id, "completed", result=aggregated)

        return {
            "task_id": task_id,
            "status": "completed",
            "execution": {
                "status": "completed",
                "worker_results": all_results,
                "waves": len(waves),
            },
            "aggregated": aggregated,
        }

    def _build_execution_waves(self, workers: list[dict], dependency_rules: dict) -> list[list[dict]]:
        """
        分析 Worker 之间的依赖关系，构建执行层级（waves）

        同一个 wave 内的 worker 无依赖关系，可以并行
        不同 wave 必须顺序执行（上一个 wave 完成后下一个 wave 才能开始）
        """
        # 建立 worker_type → worker 对象的映射
        type_to_worker = {}
        for w in workers:
            wt = w["worker_type"]
            if wt not in type_to_worker:
                type_to_worker[wt] = []
            type_to_worker[wt].append(w)

        # 拓扑排序分 wave
        waves = []
        remaining = set(w["id"] for w in workers)
        completed_types = set()

        while remaining:
            # 找所有依赖都已完成的 worker（没有依赖的也在此）
            wave = []
            for w in workers:
                if w["id"] not in remaining:
                    continue
                deps = dependency_rules.get(w["worker_type"], [])
                # 检查所有依赖类型的 worker 是否都已完成
                all_deps_done = all(
                    st in completed_types for st in deps if st in type_to_worker
                )
                if all_deps_done:
                    wave.append(w)

            if not wave:
                # 理论上不应该走到这里（环检测失败），但保底放入所有剩余
                wave = [w for w in workers if w["id"] in remaining]
                if not wave:
                    break

            waves.append(wave)
            for w in wave:
                remaining.discard(w["id"])
                completed_types.add(w["worker_type"])

        return waves

    def _inject_upstream_results(self, wave_workers: list[dict], all_results: dict) -> list[dict]:
        """
        Hand Passing：把上游 Worker 的产出注入到下游 Worker 的 goal 中
        上游结果读取自 output_dir 中的文件
        """
        import glob

        for w in wave_workers:
            upstream = w["context"].get("input_from", [])
            if not upstream:
                continue

            injected_info = []
            for up in upstream:
                up_id = up["worker_id"]
                up_result = all_results.get(up_id, {})
                up_output_dir = up["output_dir"]

                # 读取上游产出文件
                files = glob.glob(os.path.join(up_output_dir, "*"))
                files_str = "\n".join(f"  - {os.path.basename(f)}" for f in files) if files else "  （无文件）"

                injected_info.append(
                    f"Worker [{up_id}]（{up['worker_type']}）产出：\n"
                    f"  产出目录: {up_output_dir}\n"
                    f"  文件列表:\n{files_str}"
                )

                # 也把结果文本注入
                if up_result.get("result"):
                    result_text = up_result["result"][:2000]
                    injected_info.append(f"  结果摘要:\n{result_text}")

            if injected_info:
                # 在 goal 前面加上游产出信息
                injection_text = (
                    "\n\n" + "="*60 + "\n"
                    "【Hand Passing：上游Worker产出】\n"
                    + "\n\n".join(injected_info)
                    + "\n" + "="*60 + "\n\n"
                )
                # 在 _make_worker_goal 生成的 goal 中插入
                w["goal"] = injection_text + w["goal"]

        return wave_workers

    def _dict_to_result(self, d: dict):
        """把 dict 格式的 worker result 转为 WorkerResult"""
        from worker_pool import WorkerResult
        return WorkerResult(
            worker_id=d.get("worker_id", "unknown"),
            status=d.get("status", "failed"),
            result=d.get("result"),
            error=d.get("error"),
        )

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

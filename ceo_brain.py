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
from session_store import (
    create_task,
    get_task,
    update_task_status,
)
from harness import AgentSwarmHarness


# ─────────────────────────────────────────
# Skills 动态路由表
# 关键词匹配 → 最适合的 skill
# ─────────────────────────────────────────

SKILL_ROUTING = {
    # 代码开发相关
    "code_worker": [
        (["写代码", "开发", "实现", "构建", "编程"], "software-development/skill-creator"),
        (["调试", "debug", "错误", "修复bug"], "software-development/systematic-debugging"),
        (["测试", "单元测试", "test"], "software-development/test-driven-development"),
        (["实验", "验证", "spike", "探索"], "software-development/spike"),
        (["代码审查", "review", "审阅"], "software-development/requesting-code-review"),
        (["debugpy", "python调试"], "software-development/python-debugpy"),
        (["node", "js", "javascript调试"], "software-development/node-inspect-debugger"),
        (["自动化", "脚本", "批处理"], "software-development/macos-automation"),
    ],
    # PPT/演示相关
    "ppt_worker": [
        (["ppt", "演示", "幻灯片", "presentation"], "productivity/ppt-workflow"),
        (["生成ppt", "制作ppt", "创建ppt"], "productivity/ppt-generator"),
        (["创意ppt", "精美ppt", "设计ppt"], "sn-ppt-creative"),
        (["修改ppt", "编辑ppt", "更新ppt"], "productivity/powerpoint"),
    ],
    # 文档/数据相关
    "doc_worker": [
        (["excel", "表格", "csv", "数据整理", "报表"], "productivity/spreadsheet"),
        (["pdf", "扫描件", "ocr", "提取文字"], "productivity/ocr-and-documents"),
        (["notion", "笔记", "知识库"], "productivity/notion"),
        (["html", "网页报告", "web页面"], "productivity/html-generator"),
        (["文档", "报告", "整理", "归档"], "productivity/office-automation"),
        (["airtable", "数据库"], "productivity/airtable"),
    ],
    # 视频相关
    "video_worker": [
        (["视频", "剪辑", "movie", "剪辑"], "media/youtube-content"),
        (["gif", "动图"], "media/gif-search"),
        (["字幕", "语音", "配音"], "media/spotify"),  # 暂无语音skill，用spotify占位
    ],
    # UI/设计相关
    "ui_worker": [
        (["封面", "cover", "海报", "banner"], "creative/baoyu-cover-image"),
        (["知识漫画", "comic", "漫画"], "creative/baoyu-comic"),
        (["信息图", "infographic", "图表可视化"], "creative/baoyu-infographic"),
        (["小红书", "xhs", "配图"], "creative/baoyu-xhs-images"),
        (["logo", "icon", "图标设计", "vi"], "creative/pixel-art"),
    ],
    # QA/测试相关
    "qa_worker": [
        (["测试", "测试用例", "test case", "单元测试"], "software-development/test-driven-development"),
        (["质量", "评审", "代码审查"], "software-development/requesting-code-review"),
        (["web", "网站", "界面", "ui测试"], "dogfood"),
        (["调试", "debug", "排查"], "software-development/systematic-debugging"),
    ],
    # 调研相关
    "research_worker": [
        (["arxiv", "论文", "学术", "paper"], "research/arxiv"),
        (["博客", "rss", "订阅", "blog"], "research/blogwatcher"),
        (["深度调研", "市场分析", "行业研究"], "sn-deep-research"),
        (["github", "代码搜索", "搜索代码"], "sn-search-code"),
        (["搜索引擎", "搜索", "search"], "find-skills-skill/find-skills"),
    ],
}

# Worker 默认 skill（未匹配时使用）
WORKER_DEFAULT_SKILLS = {
    "code_worker": "software-development/skill-creator",
    "ppt_worker": "productivity/ppt-workflow",
    "video_worker": "media/youtube-content",
    "ui_worker": "creative/baoyu-cover-image",
    "qa_worker": "software-development/test-driven-development",
    "doc_worker": "productivity/spreadsheet",
    "research_worker": "find-skills-skill/find-skills",
    "generic_worker": "find-skills-skill/find-skills",
}


def select_skill_for_task(worker_type: str, goal: str) -> list[str]:
    """
    根据 worker_type 和具体 goal 内容，动态选择最合适的 skill(s)

    Returns: skill 路径列表（可能多个）
    """
    goal_lower = goal.lower()
    selected = []

    # 精确匹配优先
    for keywords, skill_path in SKILL_ROUTING.get(worker_type, []):
        if any(kw.lower() in goal_lower for kw in keywords):
            if skill_path not in selected:
                selected.append(skill_path)

    # 如果没有匹配，使用默认 skill
    if not selected:
        default = WORKER_DEFAULT_SKILLS.get(worker_type)
        if default:
            selected = [default]

    return selected


def build_skill_instructions(skills: list[str]) -> str:
    """
    把 skill 列表构建成 hermes chat -s 参数格式的指令
    """
    if not skills:
        return ""
    skill_str = ",".join(skills)
    return (
        f"\n\n{'='*60}\n"
        f"【Skills 指令】\n"
        f"请使用以下 skill(s) 完成本任务：{skill_str}\n"
        f"调用方式：hermes chat -s {skill_str} -q \"你的任务\"\n"
        f"{'='*60}\n"
    )


# ─────────────────────────────────────────
# CEO Brain 类
# ─────────────────────────────────────────


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

    def decompose(self, task_description: str, use_llm: bool = None) -> dict:
        """
        理解任务并拆解成子任务

        策略：
        - 如果任务简单（有关键词匹配），用规则快速拆解
        - 如果任务复杂（模糊、多意图），交给 LLM 智能理解
        """
        # 简单检测：是否有多意图信号（"和"、"然后"、"同时"、"并且"）
        multi_intent_signals = ["和", "然后", "同时", "并且", "以及", "并且", "加", "或者", "或"]
        has_complex_structure = any(sig in task_description for sig in multi_intent_signals)

        # 模糊信号：没有明确类型关键词时
        task_lower = task_description.lower()
        clear_type_kw = ["ppt", "视频", "调研", "测试", "文档", "写代码", "开发", "设计", "ui"]
        has_clear_type = any(kw in task_lower for kw in clear_type_kw)

        # 自动判断：复杂结构或无明确类型 → LLM
        if use_llm is None:
            use_llm = has_complex_structure or not has_clear_type

        if use_llm:
            print(f"[CEO] 复杂任务，启用 LLM 智能拆解...")
            decomposition = self._llm_decompose(task_description)
            if decomposition:
                return decomposition

        # 兜底到规则
        print(f"[CEO] 使用规则快速拆解...")
        return self._rule_based_decompose(task_description)

    def _llm_decompose(self, task_description: str) -> dict:
        """
        LLM 智能任务拆解
        使用 hermes chat -q 调用 LLM 理解复杂任务意图
        """
        import subprocess, json

        prompt = f"""你是一个任务拆解专家。请分析以下用户任务，拆解成可执行的子任务。

任务：{task_description}

请以 JSON 格式输出你的拆解结果，格式如下：
{{
  "task_title": "任务标题（50字内）",
  "task_description": "完整任务描述",
  "task_goal": "CEO层总体目标",
  "subtasks": [
    {{
      "title": "子任务标题",
      "goal": "给 Worker 的具体目标描述（要具体，说明在什么目录下做什么）",
      "worker_type": "code_worker/ppt_worker/doc_worker/qa_worker/ui_worker/video_worker/research_worker/generic_worker",
      "context": {{"aspect": "development/presentation/documentation/etc"}}
    }}
  ]
}}

worker_type 类型说明：
- code_worker：写代码、开发脚本、实现功能
- ppt_worker：PPT、演示文稿、幻灯片
- doc_worker：文档、报告、表格、数据整理
- qa_worker：测试、验证、质量检查、评审
- ui_worker：UI设计、海报、logo、视觉设计
- video_worker：视频剪辑、配音
- research_worker：调研、搜索、分析
- generic_worker：以上都不适合时使用

拆分原则：
1. 每个 worker 只拿自己需要完成的那部分任务，不是复制完整任务
2. 考虑任务之间的依赖关系（代码完成后才能写文档）
3. 2-4个子任务为宜，不要过度拆分
4. goal 要具体，包含项目路径和具体操作

输出 JSON，不要有其他文字："""

        # 调用 hermes chat -q 执行 LLM 理解
        try:
            result = subprocess.run(
                ["hermes", "chat", "-q", prompt, "-Q"],
                capture_output=True,
                text=True,
                timeout=60,
            )
            output = result.stdout.strip()

            # 尝试解析 JSON（可能包含在 markdown 代码块中）
            json_text = output
            # 去掉 markdown 代码块包装
            if "```json" in output:
                parts = output.split("```json")
                if len(parts) > 1:
                    json_text = parts[1].split("```")[0].strip()
            elif "```" in output:
                parts = output.split("```")
                if len(parts) > 1:
                    json_text = parts[1].strip()

            decomposition = json.loads(json_text)

            # 补充缺失字段
            if "task_goal" not in decomposition:
                decomposition["task_goal"] = f"完成用户任务：{decomposition.get('task_description', task_description)}"
            if "task_title" not in decomposition:
                decomposition["task_title"] = task_description[:50]

            # 为每个 subtask 补充 id
            for st in decomposition.get("subtasks", []):
                if "id" not in st:
                    st["id"] = f"subtask-{uuid.uuid4().hex[:6]}"
                if "goal" not in st:
                    st["goal"] = st.get("title", "通用任务")

            return decomposition

        except subprocess.TimeoutExpired:
            print("[CEO] LLM 拆解超时，回退到规则方法")
            return None
        except json.JSONDecodeError as e:
            print(f"[CEO] LLM 输出 JSON 解析失败: {e}，回退到规则方法")
            return None
        except Exception as e:
            print(f"[CEO] LLM 拆解异常: {e}，回退到规则方法")
            return None

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

    def _make_worker_goal(self, goal: str, context: dict = None, worker_type: str = None) -> str:
        """
        给 Worker 的 goal 加上执行指令前缀 + Context Passing 指令 + Skills 动态路由指令
        要求 Worker 实际执行任务，并告知上下游产出位置和可用的 skills
        """
        lines = [
            "你是一个Worker。你的职责是实际完成任务，而不是仅返回文本描述。\n",
        ]

        # ─── Skills 动态路由 ───
        skills = []
        if worker_type:
            from_task_goal = goal  # 使用原始 task goal 来匹配 skill
            skills = select_skill_for_task(worker_type, from_task_goal)
            if skills:
                lines.append(f"【Skills 可用】: {', '.join(skills)}")
                lines.append("")

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

        # ─── Skills 调用指令 ───
        if skills:
            skill_str = ",".join(skills)
            lines.append(
                f"【Skills 调用指令】\n"
                f"请使用 hermes chat -s {skill_str} 来加载对应技能后执行任务。\n"
            )

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
        # 动态推断：基于 subtasks 中实际存在的 worker_type 推断依赖
        # 规则：
        #   1. code_worker 是大多数任务的基础，最先执行（无特殊依赖）
        #   2. doc_worker 依赖 code_worker（如两者都存在）
        #   3. qa_worker 依赖 code_worker（如两者都存在）
        #   4. ppt_worker 依赖 doc_worker（如 doc 存在）或 code_worker（如只有 code）
        #   5. ui_worker/video_worker 无特殊依赖（基础层）
        #   6. research_worker 无特殊依赖（基础层）
        #   7. generic_worker 无特殊依赖（基础层）
        dependency_rules = self._infer_dependency_rules(subtasks)

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
                "goal": self._make_worker_goal(st["goal"], context, worker_type),
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
        update_task_status(task_id, "completed", result=aggregated)

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

    def _infer_dependency_rules(self, subtasks: list[dict]) -> dict[str, list[str]]:
        """
        根据 subtasks 中实际存在的 worker_type 动态推断依赖规则。

        依赖推断策略：
        1. 收集 subtasks 中所有实际出现的 worker_type
        2. 如果同时存在 code + doc → doc 依赖 code
        3. 如果同时存在 code + qa  → qa 依赖 code
        4. 如果同时存在 code + ppt：
             - 有 doc → ppt 依赖 doc
             - 无 doc → ppt 依赖 code
        5. ui/video/research 视为基础层，无上游依赖
        6. generic 视为基础层，无上游依赖

        Returns:
            dict[worker_type, list[upstream_worker_type]]
            例如: {"doc_worker": ["code_worker"], "ppt_worker": ["doc_worker"]}
        """
        present_types = {st["worker_type"] for st in subtasks}
        rules = {}

        # doc_worker 依赖 code_worker（如果两者都存在）
        if "doc_worker" in present_types and "code_worker" in present_types:
            rules["doc_worker"] = ["code_worker"]

        # qa_worker 依赖 code_worker（如果两者都存在）
        if "qa_worker" in present_types and "code_worker" in present_types:
            rules["qa_worker"] = ["code_worker"]

        # ppt_worker 依赖策略
        if "ppt_worker" in present_types:
            if "doc_worker" in present_types:
                # 有 doc 时依赖 doc（doc 是 PPT 内容来源）
                rules["ppt_worker"] = ["doc_worker"]
            elif "code_worker" in present_types:
                # 无 doc 但有 code 时依赖 code（兜底）
                rules["ppt_worker"] = ["code_worker"]

        return rules

    def _build_dependency_graph_from_context(self, workers: list[dict]) -> dict[str, list[str]]:
        """
        从每个 worker's context['input_from'] 动态构建依赖图。
        input_from 已包含 upstream worker_id 列表，直接用即可。
        覆盖/补充 hardcoded dependency_rules。
        """
        graph = {}  # worker_id -> [upstream_worker_id, ...]

        # 快速构建 worker_id → worker 对象的映射
        id_to_worker = {w["id"]: w for w in workers}

        for w in workers:
            wid = w["id"]
            input_from = w.get("context", {}).get("input_from", [])
            # input_from 是 [{worker_id, worker_type, output_dir}, ...]
            deps = [up["worker_id"] for up in input_from if up["worker_id"] in id_to_worker]
            graph[wid] = deps

        return graph

    def _build_execution_waves(self, workers: list[dict], dependency_rules: dict) -> list[list[dict]]:
        """
        分析 Worker 之间的依赖关系，构建执行层级（waves）

        优先使用动态依赖图（context['input_from']），无时才回退到 hardcoded dependency_rules。

        同一个 wave 内的 worker 无依赖关系，可以并行
        不同 wave 必须顺序执行（上一个 wave 完成后下一个 wave 才能开始）
        """
        # ── 优先：从 context['input_from'] 构建动态依赖图 ──
        # input_from 存在时用动态图（更精确），否则回退 hardcoded rules
        use_dynamic = any(w.get("context", {}).get("input_from") for w in workers)

        if use_dynamic:
            dynamic_graph = self._build_dependency_graph_from_context(workers)
        else:
            dynamic_graph = {}

        # 建立 worker_type → worker 对象的映射（用于 hardcoded 回退路径）
        type_to_worker = {}
        for w in workers:
            wt = w["worker_type"]
            if wt not in type_to_worker:
                type_to_worker[wt] = []
            type_to_worker[wt].append(w)

        # 拓扑排序分 wave
        waves = []
        remaining = {w["id"] for w in workers}
        completed_ids: set[str] = set()   # 已完成的 worker_id（用于动态图）
        completed_types: set[str] = set()  # 已完成的 worker_type（用于 hardcoded 回退）

        while remaining:
            wave = []
            for w in workers:
                if w["id"] not in remaining:
                    continue

                if use_dynamic and dynamic_graph:
                    # ── 动态依赖：检查 upstream worker_id 是否全部完成 ──
                    upstream_ids = dynamic_graph.get(w["id"], [])
                    all_deps_done = all(up_id in completed_ids for up_id in upstream_ids)
                else:
                    # ── Hardcoded 回退：检查依赖类型是否已全部完成 ──
                    deps = dependency_rules.get(w["worker_type"], [])
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
                completed_ids.add(w["id"])
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
            worker_results=execution["execution"]["worker_results"],
        )

        return {
            "task_id": execution["task_id"],
            "status": execution["status"],
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

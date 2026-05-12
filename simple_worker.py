#!/usr/bin/env python3
"""
Simple Worker - 直接调用Hermes工具的轻量执行器

设计原则（来自整体基调）：
- 自主路径推理：CEO负责理解任务、拆解分发
- 工具调用能力：Worker直接调用工具完成工作

新架构（v2）：
- CEO用LLM生成【任务指令】JSON块
- Simple Worker直接解析JSON并执行，无需正则猜测
- 支持：terminal, write_file, read_file, patch, search

Usage:
    python3 simple_worker.py '{"goal": "...", "context": {...}}'
"""

import sys
import json
import traceback
import os
import re

# 添加Hermes路径
sys.path.insert(0, '/Users/yutanglao/.hermes/hermes-agent')


def _parse_result(result):
    """Hermes工具返回JSON字符串，需要parse"""
    if isinstance(result, str):
        return json.loads(result)
    return result


def _get_tools():
    from tools.file_tools import write_file_tool, read_file_tool, patch_tool, search_tool
    from tools.terminal_tool import terminal_tool
    return write_file_tool, read_file_tool, patch_tool, search_tool, terminal_tool


def _extract_task_instruction(goal: str) -> dict:
    """
    从CEO的goal中提取【任务指令】JSON块。

    CEO生成的goal格式：
    【任务指令】
    {"tool": "...", "params": {...}, "description": "..."}
    ---

    JSON可能在单行或多行。
    """
    lines = goal.split('\n')

    # 找到【任务指令】之后的行，收集直到---
    json_lines = []
    found_marker = False
    for line in lines:
        if '【任务指令】' in line:
            found_marker = True
            continue
        if found_marker:
            if line.strip().startswith('---') or line.strip().startswith('【'):
                break
            json_lines.append(line)

    if json_lines:
        json_str = ''.join(json_lines).strip()
        if json_str:
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                pass

    # Fallback
    return {"tool": "unknown", "params": {}, "description": goal}


def execute_goal(goal: str, context) -> dict:
    """
    解析goal中的【任务指令】JSON并执行。

    CEO Brain负责用LLM生成结构化指令，
    Worker只负责解析JSON并调用工具。
    """
    # 解析context
    if isinstance(context, str):
        try:
            context = json.loads(context)
        except:
            context = {}
    elif context is None:
        context = {}

    workspace = context.get("workspace", "/tmp")
    output_dir = context.get("output_dir", workspace)
    os.makedirs(output_dir, exist_ok=True)

    write_file_tool, read_file_tool, patch_tool, search_tool, terminal_tool = _get_tools()

    # ═══════════════════════════════════════════════════════════
    # 工具路由（基于LLM生成的【任务指令】JSON）
    # ═══════════════════════════════════════════════════════════

    task = _extract_task_instruction(goal)
    tool_name = task.get("tool", "unknown")
    params = task.get("params", {})
    description = task.get("description", "")

    if tool_name == "terminal":
        cmd = params.get("command", "")
        if not cmd:
            return {"status": "error", "error": "terminal工具缺少command参数"}
        result_str = terminal_tool(command=cmd, timeout=params.get("timeout", 60), workdir=params.get("workdir"))
        result = _parse_result(result_str)
        return {
            "status": "success" if result.get("exit_code") == 0 else "error",
            "worker_output": result.get("output", ""),
            "tool": "terminal",
            "exit_code": result.get("exit_code"),
        }

    elif tool_name == "write_file":
        path = params.get("path", "")
        content = params.get("content", "")
        if not path:
            return {"status": "error", "error": "write_file工具缺少path参数"}
        escaped_content = content.replace("'", "'\\''")
        result = _parse_result(terminal_tool(command=f"echo '{escaped_content}' > '{path}'", timeout=30, workdir=None))
        size = os.path.getsize(path) if os.path.exists(path) else 0
        return {
            "status": "success",
            "worker_output": f"✅ 已写入: {path} ({size} bytes)",
            "file_path": path,
            "size": size,
            "tool": "write_file"
        }

    elif tool_name == "read_file":
        path = params.get("path", "")
        if not path:
            return {"status": "error", "error": "read_file工具缺少path参数"}
        if not os.path.exists(path):
            return {"status": "error", "error": f"文件不存在: {path}"}
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
        lines = content.count('\n')
        # 如果描述中提到"行数"或"wc"，自动统计
        if description and any(kw in description for kw in ["行数", "wc", "line"]):
            return {
                "status": "success",
                "worker_output": f"读取成功: {path} ({len(content)} bytes, {lines} lines)",
                "file_path": path,
                "content": content[:500],  # 限制返回长度
                "bytes": len(content),
                "lines": lines,
                "tool": "read_file"
            }
        return {
            "status": "success",
            "worker_output": f"读取成功: {path} ({len(content)} bytes)",
            "file_path": path,
            "content": content[:500],
            "bytes": len(content),
            "tool": "read_file"
        }

    elif tool_name == "search" or tool_name == "search_files":
        pattern = params.get("pattern", "")
        path = params.get("path", "/tmp")
        if not pattern:
            return {"status": "error", "error": "search工具缺少pattern参数"}
        # 展开 ~
        path = os.path.expanduser(path)
        cmd = f"grep -r --include='*.py' --include='*.md' --include='*.json' --include='*.txt' '{pattern}' '{path}' 2>/dev/null | head -30"
        result = _parse_result(terminal_tool(command=cmd, timeout=30, workdir=None))
        output = result.get("output", "")
        count = len(output.strip().split('\n')) if output.strip() else 0
        return {
            "status": "success",
            "worker_output": f"搜索完成: 在{path}中找到{count}处匹配",
            "matches": output[:2000],
            "match_count": count,
            "tool": "search"
        }

    elif tool_name == "patch":
        path = params.get("path", "")
        old_string = params.get("old_string", "")
        new_string = params.get("new_string", "")
        if not path or not old_string:
            return {"status": "error", "error": "patch工具缺少必要参数"}
        escaped_old = old_string.replace("'", "'\\''")
        escaped_new = new_string.replace("'", "'\\''")
        cmd = f"""python3 -c "
import re
with open('{path}', 'r') as f:
    content = f.read()
updated = content.replace({repr(old_string)}, {repr(new_string)}, 1)
with open('{path}', 'w') as f:
    f.write(updated)
print('Patched 1 occurrence')
\""" """
        result = _parse_result(terminal_tool(command=cmd, timeout=30, workdir=None))
        return {
            "status": "success",
            "worker_output": f"✅ 已修补: {path}",
            "file_path": path,
            "tool": "patch"
        }

    elif tool_name == "search":
        pattern = params.get("pattern", "")
        path = params.get("path", "/tmp")
        if not pattern:
            return {"status": "error", "error": "search工具缺少pattern参数"}
        cmd = f"grep -r '{pattern}' '{path}' 2>/dev/null | head -20"
        result = _parse_result(terminal_tool(command=cmd, timeout=30, workdir=None))
        return {
            "status": "success",
            "worker_output": result.get("output", "未找到匹配"),
            "tool": "search"
        }

    else:
        return {
            "status": "unknown",
            "worker_output": f"无法识别的工具: {tool_name}",
            "task_description": description
        }


# ═══════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════

def main():
    if len(sys.argv) < 2:
        try:
            data = json.loads(sys.stdin.read())
        except Exception as e:
            print(json.dumps({"status": "error", "error": f"无输入: {e}"}))
            sys.exit(1)
    else:
        try:
            data = json.loads(sys.argv[1])
        except Exception as e:
            print(json.dumps({"status": "error", "error": f"Invalid JSON: {e}"}))
            sys.exit(1)

    goal = data.get("goal", "")
    context = data.get("context", {})

    if not goal:
        print(json.dumps({"status": "error", "error": "Empty goal"}))
        sys.exit(1)

    try:
        result = execute_goal(goal, context)
        print(json.dumps(result, ensure_ascii=False))
        sys.exit(0 if result["status"] in ("success", "unknown") else 1)
    except Exception as e:
        error_result = {
            "status": "error",
            "error": str(e),
            "traceback": traceback.format_exc()
        }
        print(json.dumps(error_result, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

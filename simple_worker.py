#!/usr/bin/env python3
"""
Simple Worker - 直接调用Hermes工具的轻量执行器

设计原则（来自整体基调）：
- 自主路径推理：CEO负责理解任务、拆解分发
- 工具调用能力：Worker直接调用工具完成工作

新架构（v2）：
- CEO用LLM生成【任务指令】JSON块
- Simple Worker直接解析JSON并执行，无需正则猜测
- 支持：terminal, write_file, read_file, patch, search, web_search, web_extract

Usage:
    python3 simple_worker.py '{"goal": "...", "context": {...}}'
"""

import sys
import json
import traceback
import os
import re
import requests
import html as html_module

# 添加Hermes路径
sys.path.insert(0, '/Users/yutanglao/.hermes/hermes-agent')


def _parse_result(result):
    """Hermes工具返回JSON字符串，需要parse"""
    if isinstance(result, str):
        return json.loads(result)
    return result


def _get_tools():
    """所有 Worker 共享 Monica 的全部工具（同一进程空间）"""
    from tools.file_tools import write_file_tool, read_file_tool, patch_tool, search_tool
    from tools.terminal_tool import terminal_tool
    from tools.browser_tool import browser_navigate, browser_snapshot, browser_vision
    from tools.code_execution_tool import execute_code
    return (
        write_file_tool, read_file_tool, patch_tool, search_tool,
        terminal_tool,
        browser_navigate, browser_snapshot, browser_vision,
        execute_code,
    )


def ddg_search(query: str, limit: int = 5) -> str:
    """
    用 DuckDuckGo HTML API 做无认证搜索（直接调，不需要ddgs包）。
    返回格式化的搜索结果文本。
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        url = f"https://html.duckduckgo.com/html/?q={requests.utils.quote(query)}"
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        text = resp.text
        results = []
        # DuckDuckGo HTML 结果格式：<a class="result__a" href="URL">Title</a>...<a class="result__snippet" href="...">Snippet</a>
        for match in re.finditer(r'<a class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', text, re.DOTALL):
            href = match.group(1)
            title_raw = match.group(2)
            title = html_module.unescape(re.sub(r'<[^>]+>', '', title_raw))
            results.append(f"标题: {title}\n链接: {href}")
        if not results:
            return f"搜索「{query}」无结果"
        return f"搜索「{query}」，共{len(results)}条结果:\n\n" + "\n\n".join(results[:limit])
    except Exception as e:
        return f"DuckDuckGo搜索失败: {e}"


def ddg_extract(url: str) -> str:
    """用 requests 抓取网页正文（简单实现）。"""
    try:
        resp = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        }, timeout=15)
        resp.raise_for_status()
        text = resp.text
        # 移除 script/style
        text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
        # 提取 body
        body_match = re.search(r'<body[^>]*>(.*)', text, re.DOTALL)
        if body_match:
            text = body_match.group(1)
        # 去掉所有标签
        text = re.sub(r'<[^>]+>', ' ', text)
        # 还原 HTML 实体
        text = html_module.unescape(text)
        # 合并空白
        text = re.sub(r'\s+', ' ', text).strip()
        return text[:8000]
    except Exception as e:
        return f"抓取失败: {e}"


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

    (write_file_tool, read_file_tool, patch_tool, search_tool,
     terminal_tool,
     browser_navigate, browser_snapshot, browser_vision,
     execute_code) = _get_tools()

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
        if content == "" and not params.get("allow_empty", False):
            return {"status": "error", "error": f"write_file拒绝写入空内容: {path}"}
        path = os.path.expanduser(path)
        if not os.path.isabs(path):
            path = os.path.join(output_dir, path)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
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

    elif tool_name == "web_search" or tool_name == "browser_navigate":
        # browser_navigate 是通用网页访问工具，支持任何 URL
        url = params.get("url", params.get("q", ""))
        if not url:
            return {"status": "error", "error": "缺少url参数"}
        try:
            # 自动加上 https:// 如果没有协议
            if not url.startswith(('http://', 'https://')):
                url = 'https://' + url
            result_str = browser_navigate(url=url)
            result = _parse_result(result_str)
            if result.get("success"):
                snapshot = result.get("snapshot", "")
                return {
                    "status": "success",
                    "worker_output": f"✅ 访问成功: {url}\n{snapshot[:3000]}",
                    "url": url,
                    "snapshot": snapshot[:5000],
                    "tool": "browser_navigate"
                }
            else:
                return {"status": "error", "error": result.get("error", "访问失败")}
        except Exception as e:
            return {"status": "error", "error": f"browser_navigate失败: {e}"}

    elif tool_name == "web_extract" or tool_name == "browser_snapshot":
        try:
            snapshot_str = browser_snapshot()
            snapshot_result = _parse_result(snapshot_str)
            snapshot_text = snapshot_result.get("snapshot", "") if isinstance(snapshot_result, dict) else str(snapshot_result)
            return {
                "status": "success",
                "worker_output": f"页面快照:\n{snapshot_text[:5000]}",
                "snapshot": snapshot_text[:5000],
                "tool": "browser_snapshot"
            }
        except Exception as e:
            return {"status": "error", "error": f"browser_snapshot失败: {e}"}

    elif tool_name == "browser_vision":
        question = params.get("question", params.get("query", "这张截图里有什么关键信息？"))
        try:
            vision_str = browser_vision(question=question)
            vision_result = _parse_result(vision_str)
            if isinstance(vision_result, dict) and vision_result.get("success"):
                return {
                    "status": "success",
                    "worker_output": f"📸 视觉分析:\n{vision_result.get('analysis', '')}",
                    "analysis": vision_result.get("analysis", "")[:5000],
                    "tool": "browser_vision"
                }
            else:
                return {"status": "error", "error": vision_result.get("error", "browser_vision失败")}
        except Exception as e:
            return {"status": "error", "error": f"browser_vision失败: {e}"}

    elif tool_name == "execute_code" or tool_name == "code_execution":
        code = params.get("code", params.get("python", ""))
        if not code:
            return {"status": "error", "error": "execute_code缺少code参数"}
        try:
            result_str = execute_code(code=code)
            result = _parse_result(result_str)
            output = result.get("result", "") if isinstance(result, dict) else str(result)
            return {
                "status": "success",
                "worker_output": f"代码执行结果:\n{output[:5000]}",
                "execution_result": output[:5000],
                "tool": "execute_code"
            }
        except Exception as e:
            return {"status": "error", "error": f"execute_code失败: {e}"}

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

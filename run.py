#!/usr/bin/env python3
"""
Agent Swarm CLI Entry Point
用法:
  python3 run.py status <task_id>       # 查看任务状态
  python3 run.py log <task_id>         # 查看任务日志
  python3 run.py tasks                  # 列出所有任务
  python3 run.py test                  # 运行测试
  python3 run.py "任务描述"            # 执行任务
"""

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from ceo_brain import CEOBrain
from harness import AgentSwarmHarness
from session_store import get_task, get_task_workers, get_event_log, get_all_tasks, get_task_stats
import json


def cmd_status(task_id: str):
    harness = AgentSwarmHarness()
    status = harness.get_task_status(task_id)
    task = status["task"]
    workers = status["workers"]
    stats = status["stats"]

    print(f"\n=== Task: {task['title']} ===")
    print(f"ID: {task['id']}")
    print(f"Status: {task['status']}")
    print(f"Created: {task['created_at']}")
    print(f"\nWorker Stats: {stats}")
    print(f"\nWorkers:")
    for w in workers:
        icon = {"pending": "⏳", "running": "🔄", "completed": "✅", "failed": "❌", "cancelled": "🚫"}.get(w["status"], "?")
        print(f"  {icon} [{w['worker_type']}] {w['id']} - {w['status']} (retries: {w['retry_count']})")
        if w.get("error"):
            print(f"     Error: {w['error'][:100]}")

    if task.get("result"):
        print(f"\nResult:\n{task['result'][:1000]}")


def cmd_log(task_id: str):
    events = get_event_log(entity_id=task_id)
    print(f"\n=== Event Log for Task {task_id} ===")
    for e in events:
        print(f"[{e['created_at']}] {e['entity_type']}:{e['entity_id']} - {e['event']}")


def cmd_tasks():
    tasks = get_all_tasks()
    print(f"\n=== All Tasks ({len(tasks)}) ===")
    for t in tasks[:20]:
        icon = {"pending": "⏳", "running": "🔄", "completed": "✅", "failed": "❌", "cancelled": "🚫"}.get(t["status"], "?")
        print(f"  {icon} [{t['status']}] {t['title'][:50]} - {t['created_at']}")


def cmd_test():
    print("\n=== Agent Swarm Self-Test ===")
    print("测试任务: 写一个简单的Python计算器程序")

    ceo = CEOBrain()
    result = ceo.run_full_flow("写一个简单的Python计算器程序，支持加减乘除", parallel=True)

    print("\n" + "="*60)
    print("FINAL REPORT:")
    print("="*60)
    print(result["report"])
    print(f"\nTask ID: {result['task_id']}")


def cmd_exec(task_desc: str):
    print(f"\n=== Executing: {task_desc} ===")
    ceo = CEOBrain()
    result = ceo.run_full_flow(task_desc, parallel=True)
    print("\n" + "="*60)
    print(result["report"])
    print(f"\nTask ID: {result['task_id']}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "status" and len(sys.argv) >= 3:
        cmd_status(sys.argv[2])
    elif cmd == "log" and len(sys.argv) >= 3:
        cmd_log(sys.argv[2])
    elif cmd == "tasks":
        cmd_tasks()
    elif cmd == "test":
        cmd_test()
    else:
        # 执行任务
        cmd_exec(" ".join(sys.argv[1:]))

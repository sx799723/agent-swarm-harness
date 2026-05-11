#!/usr/bin/env python3
"""
Agent Swarm Worker Pool
Worker 生命周期管理 — 启动、执行、监控、销毁
"""

import subprocess
import uuid
import json
import time
import threading
from typing import Optional

# 动态导入，避免循环引用
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

# ─────────────────────────────────────────
# Worker 类型 → Skill 映射
# ─────────────────────────────────────────

WORKER_TYPE_SKILLS = {
    "ppt_worker": "ppt-workflow",
    "code_worker": "software-development/skill-creator",
    "video_worker": "media/youtube-content",
    "ui_worker": "creative/baoyu-comic",
    "qa_worker": "software-development/test-driven-development",
    "doc_worker": "productivity/spreadsheet",
    "generic_worker": None,  # 无专属 skill，用通用配置
}

# Worker 类型 → Hermes profile 映射（如果需要隔离配置）
WORKER_TYPE_PROFILE = {
    "ppt_worker": "swarm-worker",
    "code_worker": "swarm-worker",
    "video_worker": "swarm-worker",
    "ui_worker": "swarm-worker",
    "qa_worker": "swarm-worker",
    "doc_worker": "swarm-worker",
    "generic_worker": "swarm-worker",
}

# 默认工具集
DEFAULT_TOOLSETS = ["terminal", "file", "code_execution", "web", "search", "browser", "vision"]


class WorkerResult:
    def __init__(self, worker_id: str, status: str, result: str = None, error: str = None, session_id: str = None):
        self.worker_id = worker_id
        self.status = status  # completed / failed
        self.result = result
        self.error = error
        self.session_id = session_id


class WorkerPool:
    """
    Worker 生命周期管理
    支持两种执行模式：
    1. delegate_task（内存内并发，推荐用于短任务）
    2. hermes chat -q（子进程，用于长任务或需要独立环境）
    """

    def __init__(self):
        self._running_workers: dict[str, subprocess.Popen] = {}
        self._results: dict[str, WorkerResult] = {}
        self._lock = threading.Lock()

    def spawn(self, worker_id: str, worker_type: str, goal: str, context: dict = None, max_retries: int = 3) -> WorkerResult:
        """
        启动一个 worker 执行任务
        返回 WorkerResult
        """
        print(f"[WorkerPool] Spawning {worker_id} (type={worker_type})")
        print(f"[WorkerPool] Goal: {goal[:100]}...")

        # 构建 skill 标志
        skill = WORKER_TYPE_SKILLS.get(worker_type)
        skill_flag = f"-s {skill}" if skill else ""

        # 拼接 context 为系统提示补充
        context_str = ""
        if context:
            context_str = f"\n\n[额外上下文]\n{json.dumps(context, ensure_ascii=False)}"

        # 完整的 prompt（带上 context）
        full_goal = goal + context_str

        # 构建 hermes chat -q 命令
        # Worker 执行时复用当前环境的 API 配置（不指定 profile）
        # 这样 Worker 会继承当前 shell 环境的 .env 配置
        cmd = f"hermes chat {skill_flag} -q {json.dumps(full_goal)}"

        print(f"[WorkerPool] Executing: {cmd[:120]}...")

        try:
            # 启动子进程
            proc = subprocess.Popen(
                cmd,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=os.path.expanduser("~")
            )

            with self._lock:
                self._running_workers[worker_id] = proc

            # 等待完成（同步等待）
            stdout, stderr = proc.communicate(timeout=600)  # 10分钟超时

            with self._lock:
                if worker_id in self._running_workers:
                    del self._running_workers[worker_id]

            if proc.returncode == 0:
                result = WorkerResult(
                    worker_id=worker_id,
                    status="completed",
                    result=stdout[:5000] if stdout else "执行完成，无输出",
                    session_id=worker_id  # 用 worker_id 作为 session_id
                )
            else:
                result = WorkerResult(
                    worker_id=worker_id,
                    status="failed",
                    result=stdout[:5000] if stdout else None,
                    error=stderr[:2000] if stderr else f"Exit code: {proc.returncode}",
                    session_id=worker_id
                )

            self._results[worker_id] = result
            print(f"[WorkerPool] {worker_id} finished with status={result.status}")
            return result

        except subprocess.TimeoutExpired:
            proc.kill()
            with self._lock:
                if worker_id in self._running_workers:
                    del self._running_workers[worker_id]
            result = WorkerResult(
                worker_id=worker_id,
                status="failed",
                error=f"执行超时（10分钟）"
            )
            self._results[worker_id] = result
            print(f"[WorkerPool] {worker_id} timed out")
            return result

        except Exception as e:
            with self._lock:
                if worker_id in self._running_workers:
                    del self._running_workers[worker_id]
            result = WorkerResult(
                worker_id=worker_id,
                status="failed",
                error=str(e)
            )
            self._results[worker_id] = result
            print(f"[WorkerPool] {worker_id} failed: {e}")
            return result

    def get_result(self, worker_id: str) -> Optional[WorkerResult]:
        """获取 worker 执行结果"""
        return self._results.get(worker_id)

    def is_running(self, worker_id: str) -> bool:
        """检查 worker 是否还在运行"""
        with self._lock:
            return worker_id in self._running_workers

    def kill(self, worker_id: str):
        """强制终止 worker"""
        with self._lock:
            if worker_id in self._running_workers:
                self._running_workers[worker_id].kill()
                del self._running_workers[worker_id]
                print(f"[WorkerPool] Killed {worker_id}")

    def kill_all(self):
        """终止所有 worker"""
        with self._lock:
            for wid, proc in self._running_workers.items():
                proc.kill()
                print(f"[WorkerPool] Killed {wid}")
            self._running_workers.clear()

    def get_running_count(self) -> int:
        """获取正在运行的 worker 数量"""
        with self._lock:
            return len(self._running_workers)


# 全局单例
_global_pool: Optional[WorkerPool] = None


def get_worker_pool() -> WorkerPool:
    global _global_pool
    if _global_pool is None:
        _global_pool = WorkerPool()
    return _global_pool

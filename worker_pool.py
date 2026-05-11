#!/usr/bin/env python3
"""
Agent Swarm Worker Pool
Worker 生命周期管理

核心职责：
1. spawn(worker)     — 启动一个worker（异步）
2. kill(worker_id)   — 终止worker
3. get_result()      — 获取结果（非阻塞）
4. kill_all()        — 全部终止

设计原则：
- spawn() 是非阻塞的：启动后立即返回 worker_id
- 结果通过回调或轮询获取
- 每个worker跑在独立子进程中
"""

import json
import subprocess
import threading
import uuid
import sys
import os
import time
import dataclasses
from typing import Optional, Callable

sys.path.insert(0, os.path.dirname(__file__))
from config import PROJECT_ROOT


# ─────────────────────────────────────────
# Worker 类型 → Skill 映射
# ─────────────────────────────────────────

WORKER_TYPE_SKILLS = {
    "code_worker":     "software-development/skill-creator",
    "ppt_worker":      "productivity/ppt-workflow",
    "video_worker":    "media/youtube-content",
    "ui_worker":       "creative/baoyu-comic",
    "qa_worker":       "software-development/test-driven-development",
    "doc_worker":      "productivity/spreadsheet",
    "research_worker": "research/arxiv",
    "generic_worker":  "",
}


# ─────────────────────────────────────────
# 日志解析
# ─────────────────────────────────────────

import re

LOG_PATTERN = re.compile(r"^\[(INFO|WARN|ERROR|DEBUG)\]\s*(.*)$", re.IGNORECASE)
PROGRESS_PATTERN = re.compile(r"^\[PROGRESS\]\s*(\d+(?:\.\d+)?)\s*(?:[-:]\s*(.*))?$", re.IGNORECASE)


def parse_worker_output(stdout: bytes, stderr: bytes) -> tuple[list[dict], float, str]:
    """
    解析 worker 输出，提取分级日志、进度、最终结果

    Returns:
        logs: [{"level": "INFO"|"WARN"|"ERROR", "ts": float, "msg": str}, ...]
        progress: 0.0~1.0
        final_output: 去掉日志行后的原始输出（用于result）
    """
    logs = []
    progress = 0.0
    output_lines = []

    for raw_line in (stdout or b"").split(b"\n"):
        line = raw_line.decode("utf-8", errors="replace").rstrip()
        if not line:
            continue

        # 进度条：[PROGRESS] 0.75 - 正在下载...
        prog_match = PROGRESS_PATTERN.match(line)
        if prog_match:
            progress = float(prog_match.group(1))
            logs.append({"level": "PROGRESS", "ts": time.time(), "msg": line})
            continue

        # 分级日志：[INFO] / [WARN] / [ERROR]
        log_match = LOG_PATTERN.match(line)
        if log_match:
            level = log_match.group(1).upper()
            msg = log_match.group(2)
            logs.append({"level": level, "ts": time.time(), "msg": msg})
        else:
            output_lines.append(line)

    return logs, progress, "\n".join(output_lines)


# ─────────────────────────────────────────
# Worker Result 数据类
# ─────────────────────────────────────────

@dataclasses.dataclass
class WorkerResult:
    worker_id: str
    status: str  # "running" | "completed" | "failed" | "timeout" | "cancelled"
    result: Optional[str] = None
    error: Optional[str] = None
    session_id: Optional[str] = None
    completed_at: Optional[float] = None
    # 可观测性增强
    logs: list = dataclasses.field(default_factory=list)  # [{"level","ts","msg"},...]
    progress: float = 0.0  # 0.0~1.0


# ─────────────────────────────────────────
# Worker Pool 主类
# ─────────────────────────────────────────

class WorkerPool:
    """
    Worker 生命周期管理器

    关键设计：
    - 每个 worker 跑在独立子进程中（hermes chat -q）
    - spawn() 立即返回，不等待完成
    - 结果通过轮询或回调获取
    - 最大并发数由 Harness 控制在 ThreadPoolExecutor 层
    """

    def __init__(self):
        # worker_id → {proc, started_at, result, callback}
        self._workers: dict[str, dict] = {}
        self._lock = threading.Lock()

    def spawn(
        self,
        worker_id: str,
        worker_type: str,
        goal: str,
        context: dict = None,
        max_retries: int = 3,
        timeout: int = 7200,
        priority: int = 0,
        on_complete: Callable[[WorkerResult], None] = None,
    ) -> str:
        """
        启动一个 worker（异步，不阻塞）

        Args:
            worker_id:    唯一标识
            worker_type:   worker类型（决定用哪个skill）
            goal:          任务描述
            context:       额外上下文（传递给worker）
            max_retries:   最大重试次数（预留）
            timeout:       执行超时（秒），默认7200（2小时）
            priority:      优先级，数值越高越先调度，默认0
            on_complete:   执行完成时的回调函数

        Returns:
            worker_id（与传入的一致）
        """
        skill = WORKER_TYPE_SKILLS.get(worker_type, "")
        skill_flag = f"-s {skill}" if skill else ""

        # 拼接 context
        context_str = ""
        if context:
            context_str = f"\n\n[额外上下文]\n{json.dumps(context, ensure_ascii=False)}"

        full_goal = goal + context_str

        # 构建命令
        cmd = f"hermes chat {skill_flag} -q {json.dumps(full_goal)} --quiet"

        print(f"[WorkerPool] Spawning {worker_id} (type={worker_type})")
        print(f"[WorkerPool] Goal: {goal[:80]}...")

        # 启动子进程
        proc = subprocess.Popen(
            cmd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=PROJECT_ROOT,
        )

        with self._lock:
            self._workers[worker_id] = {
                "proc": proc,
                "started_at": time.time(),
                "worker_type": worker_type,
                "goal": goal,
                "result": None,
                "on_complete": on_complete,
                "timeout": timeout,
                "priority": priority,
            }

        # 在独立线程中监控结果（非阻塞）
        thread = threading.Thread(
            target=self._monitor,
            args=(worker_id, timeout),
            daemon=True,
        )
        thread.start()

        return worker_id

    def _monitor(self, worker_id: str, timeout: int = 7200):
        """
        监控子进程，完成后设置结果并调用回调
        在独立线程中运行，不阻塞其他worker

        Args:
            worker_id: worker标识
            timeout: 超时秒数（从spawn传入）
        """
        with self._lock:
            if worker_id not in self._workers:
                return
            proc = self._workers[worker_id]["proc"]

        try:
            stdout, stderr = proc.communicate(timeout=timeout)  # 动态超时
            logs, progress, final_output = parse_worker_output(stdout, stderr)

            if proc.returncode == 0:
                result = WorkerResult(
                    worker_id=worker_id,
                    status="completed",
                    result=final_output if final_output.strip() else "执行完成",
                    logs=logs,
                    progress=1.0,
                    session_id=worker_id,
                    completed_at=time.time(),
                )
            else:
                result = WorkerResult(
                    worker_id=worker_id,
                    status="failed",
                    result=final_output if final_output.strip() else None,
                    error=(stderr or b"").decode("utf-8", errors="replace").strip() or f"Exit code: {proc.returncode}",
                    logs=logs,
                    progress=progress,
                    session_id=worker_id,
                    completed_at=time.time(),
                )

        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, _ = proc.communicate()
            logs, progress, final_output = parse_worker_output(stdout, "执行超时（2小时）".encode())
            result = WorkerResult(
                worker_id=worker_id,
                status="timeout",
                result=final_output if final_output.strip() else None,
                error="执行超时（2小时）",
                logs=logs,
                progress=progress,
                completed_at=time.time(),
            )

        except Exception as e:
            result = WorkerResult(
                worker_id=worker_id,
                status="failed",
                error=str(e),
                completed_at=time.time(),
            )

        # 写入结果，调用回调
        with self._lock:
            if worker_id in self._workers:
                self._workers[worker_id]["result"] = result
                callback = self._workers[worker_id].get("on_complete")
                if callback:
                    try:
                        callback(result)
                    except Exception:
                        pass  # 回调出错不影响主流程

        print(f"[WorkerPool] {worker_id} finished with status={result.status}")

    def wait_for_result(self, worker_id: str, poll_interval: float = 2.0, timeout: float = None) -> Optional[WorkerResult]:
        """
        阻塞等待某个 worker 完成，返回结果
        """
        start = time.time()
        while True:
            result = self.get_result(worker_id)
            if result is not None:
                return result
            if timeout and (time.time() - start) > timeout:
                return None
            time.sleep(poll_interval)

    def get_result(self, worker_id: str) -> Optional[WorkerResult]:
        """
        非阻塞获取 worker 结果
        Returns: WorkerResult if done, None if still running
        """
        with self._lock:
            if worker_id not in self._workers:
                return None
            return self._workers[worker_id].get("result")

    def is_running(self, worker_id: str) -> bool:
        """检查 worker 是否还在运行"""
        with self._lock:
            if worker_id not in self._workers:
                return False
            proc = self._workers[worker_id]["proc"]
            return proc.poll() is None

    def kill(self, worker_id: str):
        """强制终止 worker"""
        with self._lock:
            if worker_id not in self._workers:
                return
            proc = self._workers[worker_id]["proc"]

        try:
            proc.terminate()
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

        with self._lock:
            self._workers[worker_id]["result"] = WorkerResult(
                worker_id=worker_id,
                status="cancelled",
                error="被手动终止",
                completed_at=time.time(),
            )
        print(f"[WorkerPool] {worker_id} killed")

    def kill_all(self):
        """终止所有 worker"""
        with self._lock:
            worker_ids = list(self._workers.keys())

        for wid in worker_ids:
            self.kill(wid)
        print(f"[WorkerPool] All workers killed ({len(worker_ids)} total)")

    def list_workers(self) -> list[str]:
        """列出所有 worker ID"""
        with self._lock:
            return list(self._workers.keys())

    def get_pool_status(self) -> dict:
        """获取 Worker Pool 状态"""
        with self._lock:
            running = sum(1 for w in self._workers.values() if w["proc"].poll() is None)
            done = len(self._workers) - running
            return {
                "total": len(self._workers),
                "running": running,
                "done": done,
            }


# ─────────────────────────────────────────
# 全局单例
# ─────────────────────────────────────────

_pool_instance: Optional[WorkerPool] = None
_pool_lock = threading.Lock()


def get_worker_pool() -> WorkerPool:
    """获取 Worker Pool 单例"""
    global _pool_instance
    with _pool_lock:
        if _pool_instance is None:
            _pool_instance = WorkerPool()
        return _pool_instance

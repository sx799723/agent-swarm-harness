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
- 实时日志通过 log_sink 回调推送，无需轮询
"""

import json
import subprocess
import threading
import uuid
import sys
import os
import time
import dataclasses
from typing import Optional, Callable, Any

sys.path.insert(0, os.path.dirname(__file__))
from config import PROJECT_ROOT


# ─────────────────────────────────────────
# Worker 类型 → Skill 映射
# ─────────────────────────────────────────

WORKER_TYPE_SKILLS = {
    "code_worker":     "code-execution",           # 专门执行代码修改任务
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
# Worker 数据类
# ─────────────────────────────────────────

@dataclasses.dataclass
class Worker:
    """
    Worker 运行时状态数据类。

    属性：
        worker_id:      唯一标识
        worker_type:    worker类型
        goal:           任务描述
        status:         running | completed | failed | timeout | cancelled
        timeout_seconds: 执行超时秒数（从spawn传入）
        priority:       优先级，数值越高越先调度
        started_at:     启动时间戳
        result:         WorkerResult（完成后填充）
        on_complete:    完成回调
        log_sink:       日志回调 Callable[[str], None] — 接收原始日志行
    """
    worker_id: str
    worker_type: str
    goal: str
    status: str = "running"
    timeout_seconds: int = 7200
    priority: int = 0
    started_at: float = dataclasses.field(default_factory=time.time)
    result: Optional[Any] = None
    on_complete: Optional[Callable[[Any], None]] = None
    log_sink: Optional[Callable[[str], None]] = None

    # 内部积累
    stdout_lines: list = dataclasses.field(default_factory=list)
    stderr_lines: list = dataclasses.field(default_factory=list)
    proc: Optional[Any] = dataclasses.field(default=None, repr=False)
    max_retries: int = 3


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
    - 实时日志通过 log_sink 回调推送，无需轮询
    - 新增 set_log_sink(sink_callback) 全局日志接收器
    - Worker 数据类携带 timeout_seconds / priority 属性
    """

    def __init__(self):
        # worker_id → Worker 实例
        self._workers: dict[str, Worker] = {}
        self._lock = threading.Lock()
        # 全局日志sink：每条原始日志行实时推送至此回调
        # 签名: Callable[[str], None] — 参数为 (worker_id, raw_log_line)
        self._log_sink: Optional[Callable[[str, str], None]] = None

    # ─────────────────────────────────────────
    # 全局日志接收器 API
    # ─────────────────────────────────────────

    def set_log_sink(self, sink: Callable[[str, str], None]):
        """
        设置全局日志接收器。

        Args:
            sink: 回调函数，签名 (worker_id: str, raw_log_line: str) -> None
                  每次 worker 输出任意一行（stdout 或 stderr）时就触发，
                  raw_log_line 为该行的原始字符串。
                  设为 None 可清除。
        """
        self._log_sink = sink

    # ─────────────────────────────────────────
    # spawn — 启动 worker
    # ─────────────────────────────────────────

    def spawn(
        self,
        worker_id: str,
        worker_type: str,
        goal: str,
        context: dict = None,
        max_retries: int = 3,
        timeout_seconds: int = 7200,
        priority: int = 0,
        on_complete: Callable[[WorkerResult], None] = None,
    ) -> str:
        """
        启动一个 worker（异步，不阻塞）

        Args:
            worker_id:       唯一标识
            worker_type:     worker类型（决定用哪个skill）
            goal:            任务描述
            context:         额外上下文（传递给worker）
            max_retries:     最大重试次数（预留）
            timeout_seconds: 执行超时（秒），默认7200（2小时）
            priority:        优先级，数值越高越先调度，默认0
            on_complete:     执行完成时的回调函数

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

        print(f"[WorkerPool] Spawning {worker_id} (type={worker_type}, priority={priority})")
        print(f"[WorkerPool] Goal: {goal[:80]}...")

        # 启动子进程（使用 PIPE 以便实时读取 stdout/stderr）
        proc = subprocess.Popen(
            cmd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True,
            bufsize=1,   # 行缓冲，实时读取
            cwd=PROJECT_ROOT,
        )

        with self._lock:
            self._workers[worker_id] = Worker(
                worker_id=worker_id,
                worker_type=worker_type,
                goal=goal,
                status="running",
                timeout_seconds=timeout_seconds,
                priority=priority,
                started_at=time.time(),
                on_complete=on_complete,
                log_sink=self._log_sink,
                proc=proc,
            )

        # 在独立线程中监控结果（非阻塞）
        thread = threading.Thread(
            target=self._monitor,
            args=(worker_id, timeout_seconds),
            daemon=True,
        )
        thread.start()

        return worker_id

    # ─────────────────────────────────────────
    # _monitor — 实时监控 + 超时取消
    # ─────────────────────────────────────────

    def _monitor(self, worker_id: str, timeout: int = 7200):
        """
        监控子进程，逐行实时推送日志，完成后设置结果并调用回调。
        在独立线程中运行，不阻塞其他 worker。

        Args:
            worker_id: worker标识
            timeout: 超时秒数（从spawn传入）
        """
        with self._lock:
            if worker_id not in self._workers:
                return
            worker = self._workers[worker_id]
            proc = worker.proc
            log_sink = worker.log_sink

        stdout_lines = []
        stderr_lines = []
        deadline = time.time() + timeout

        try:
            import select

            while True:
                # 检查是否超时（每次循环检查）
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise TimeoutError(f"执行超时（{timeout}s）")

                # 用 select 做非阻塞检查：stdout/stderr 是否有数据可读
                rlist, _, xlist = select.select(
                    [proc.stdout, proc.stderr], [], [], min(0.5, max(0.0, remaining))
                )

                if xlist:
                    pass  # 异常条件，忽略

                for fd in rlist:
                    if fd == proc.stdout:
                        line = proc.stdout.readline()
                        if not line:  # EOF
                            continue
                        line = line.rstrip()
                        stdout_lines.append(line)
                        self._emit_log(log_sink, worker_id, line)
                    elif fd == proc.stderr:
                        line = proc.stderr.readline()
                        if not line:
                            continue
                        line = line.rstrip()
                        stderr_lines.append(line)
                        self._emit_log(log_sink, worker_id, line)

                # 检查进程是否已结束且无更多输出
                if proc.poll() is not None:
                    # 读取剩余输出
                    for fd in [proc.stdout, proc.stderr]:
                        if fd is not None:
                            try:
                                while True:
                                    chunk = fd.read()
                                    if not chunk:
                                        break
                                    for line in chunk.splitlines():
                                        line = line.rstrip()
                                        if fd == proc.stdout:
                                            stdout_lines.append(line)
                                        else:
                                            stderr_lines.append(line)
                                        self._emit_log(log_sink, worker_id, line)
                            except Exception:
                                pass
                    break

            stdout_text = "\n".join(stdout_lines)
            stderr_text = "\n".join(stderr_lines)

        except TimeoutError as e:
            proc.kill()
            stdout_text = "\n".join(stdout_lines)
            stderr_text = str(e)

        except Exception as e:
            stdout_text = "\n".join(stdout_lines)
            stderr_text = str(e)

        # Determine final status based on exit code
        proc_state = proc.poll()
        if proc_state is None:
            # Process still running (shouldn't happen after loop break), treat as unknown
            final_status = "failed"
            final_error = "Process still running after monitor loop exit"
        elif proc_state == 0:
            final_status = "completed"
            final_error = None
        else:
            final_status = "failed"
            final_error = stderr_text.strip() or f"Exit code: {proc_state}"

        logs, progress, final_output = parse_worker_output(
            stdout_text.encode(), stderr_text.encode()
        )

        if final_status == "completed":
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
                error=final_error,
                logs=logs,
                progress=progress,
                session_id=worker_id,
                completed_at=time.time(),
            )

        # 写入结果，调用回调
        with self._lock:
            if worker_id in self._workers:
                self._workers[worker_id].result = result
                self._workers[worker_id].stdout_lines = stdout_lines
                self._workers[worker_id].stderr_lines = stderr_lines
                self._workers[worker_id].status = result.status
                callback = self._workers[worker_id].on_complete
                if callback:
                    try:
                        callback(result)
                    except Exception:
                        pass  # 回调出错不影响主流程

        print(f"[WorkerPool] {worker_id} finished with status={result.status}")

    def _emit_log(self, log_sink, worker_id: str, raw_line: str):
        """解析单行并通过 log_sink 实时推送"""
        if not log_sink:
            return
        try:
            log_sink(worker_id, raw_line)
        except Exception:
            pass  # sink 出错不影响 worker 执行

    # ─────────────────────────────────────────
    # 查询 & 控制 API
    # ─────────────────────────────────────────

    def wait_for_result(self, worker_id: str, poll_interval: float = 2.0, timeout: float = None) -> Optional[WorkerResult]:
        """阻塞等待某个 worker 完成，返回结果"""
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
            return self._workers[worker_id].result

    def is_running(self, worker_id: str) -> bool:
        """检查 worker 是否还在运行"""
        with self._lock:
            if worker_id not in self._workers:
                return False
            proc = self._workers[worker_id].proc
            return proc.poll() is None

    def kill(self, worker_id: str):
        """强制终止 worker"""
        with self._lock:
            if worker_id not in self._workers:
                return
            proc = self._workers[worker_id].proc

        try:
            proc.terminate()
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

        with self._lock:
            self._workers[worker_id].result = WorkerResult(
                worker_id=worker_id,
                status="cancelled",
                error="被手动终止",
                completed_at=time.time(),
            )
            self._workers[worker_id].status = "cancelled"
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

    def get_logs(self, worker_id: str) -> list[dict]:
        """
        获取 worker 当前已积累的所有日志（非阻塞）。
        Returns: [{"level": str, "ts": float, "msg": str}, ...]
        """
        with self._lock:
            if worker_id not in self._workers:
                return []
            worker = self._workers[worker_id]

        logs = []
        for line in worker.stdout_lines:
            prog_match = PROGRESS_PATTERN.match(line)
            if prog_match:
                logs.append({
                    "level": "PROGRESS", "ts": time.time(), "msg": line,
                    "progress": float(prog_match.group(1))
                })
            else:
                log_match = LOG_PATTERN.match(line)
                if log_match:
                    logs.append({
                        "level": log_match.group(1).upper(),
                        "ts": time.time(), "msg": log_match.group(2)
                    })
                else:
                    logs.append({"level": "STDOUT", "ts": time.time(), "msg": line})
        for line in worker.stderr_lines:
            logs.append({"level": "STDERR", "ts": time.time(), "msg": line})
        return logs

    def get_worker(self, worker_id: str) -> Optional[Worker]:
        """获取 Worker 数据类实例（含 timeout_seconds / priority）"""
        with self._lock:
            return self._workers.get(worker_id)

    def get_pool_status(self) -> dict:
        """获取 Worker Pool 状态（含优先级信息）"""
        with self._lock:
            workers_info = []
            running = 0
            done = 0
            for wid, w in self._workers.items():
                proc_state = w.proc.poll()
                is_running = proc_state is None
                if is_running:
                    running += 1
                else:
                    done += 1
                workers_info.append({
                    "worker_id": wid,
                    "worker_type": w.worker_type,
                    "timeout_seconds": w.timeout_seconds,
                    "priority": w.priority,
                    "status": "running" if is_running else ("done" if proc_state == 0 else "failed"),
                    "elapsed_s": time.time() - w.started_at,
                })
            # 按优先级和运行状态排序
            workers_info.sort(key=lambda x: (x["status"] == "running", x["priority"]), reverse=True)
            return {
                "total": len(self._workers),
                "running": running,
                "done": done,
                "workers": workers_info,
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

#!/usr/bin/env python3
"""
Agent Swarm Harness
调度层 — 连接 CEO Brain 和 Worker Pool 的核心调度器
"""

import uuid
import time
import threading
import concurrent.futures
from typing import Optional

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from session_store import (
    create_task,
    create_worker,
    get_task,
    get_task_workers,
    update_task_status,
    update_worker_status,
    increment_worker_retry,
    get_worker,
    assign_worker_to_task,
    is_all_workers_done,
    get_task_stats,
)
from worker_pool import WorkerPool, get_worker_pool, WorkerResult


class AgentSwarmHarness:
    """
    调度层主逻辑

    CEO Brain 通过这个 Harness 来：
    1. 创建任务（Task）
    2. 分发 Worker
    3. 监控执行
    4. 失败重试
    5. 汇总结果
    """

    def __init__(self, max_concurrent: int = 7, max_retries: int = 3, model: str = None):
        """
        Args:
            max_concurrent: 最大并发 Worker 数
            max_retries: 最大重试次数
            model: 可选的模型名称（传递给 WorkerPool，目前为预留参数）
        """
        self.max_concurrent = max_concurrent
        self.max_retries = max_retries
        self.model = model
        self._pool = get_worker_pool()
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_concurrent)

    # ─────────────────────────────────────────
    # 核心 API
    # ─────────────────────────────────────────

    def create_task(self, title: str, description: str, goal: str, parent_id: str = None) -> str:
        """
        创建一个大任务（由 CEO 调用）
        Returns: task_id
        """
        task_id = create_task(title, description, goal, parent_id)
        print(f"[Harness] Task created: {task_id} - {title}")
        return task_id

    def dispatch(self, task_id: str, workers: list[dict]) -> list[str]:
        """
        分发 Workers（由 CEO 拆解后调用）

        workers 格式:
        [
            {
                "id": "worker-uuid",           # 可选，不提供则自动生成
                "worker_type": "code_worker",
                "goal": "写一个用户注册API",
                "context": {"files": [...]},
                "max_retries": 3
            },
            ...
        ]

        Returns: [worker_id, ...]
        """
        task = get_task(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")

        worker_ids = []

        for w in workers:
            worker_id = w.get("id") or f"worker-{uuid.uuid4().hex[:8]}"
            worker_type = w.get("worker_type", "generic_worker")
            goal = w["goal"]
            context = w.get("context", {})
            max_retries = w.get("max_retries", self.max_retries)

            # 创建 worker 记录
            create_worker(
                task_id=task_id,
                worker_id=worker_id,
                worker_type=worker_type,
                goal=goal,
                context=context,
                max_retries=max_retries,
                timeout=w.get("timeout", 7200),
                priority=w.get("priority", 0),
            )
            assign_worker_to_task(task_id, worker_id)
            worker_ids.append(worker_id)

        print(f"[Harness] Dispatched {len(worker_ids)} workers for task {task_id}")
        return worker_ids

    def execute_worker(self, worker_id: str) -> WorkerResult:
        """
        执行单个 Worker（内部用）
        """
        worker = get_worker(worker_id)
        if not worker:
            raise ValueError(f"Worker {worker_id} not found")

        # 更新状态为 running
        update_worker_status(worker_id, "running")

        # 调用 Worker Pool 执行
        result = self._pool.spawn(
            worker_id=worker_id,
            worker_type=worker["worker_type"],
            goal=worker["goal"],
            context=worker.get("context"),
            max_retries=worker.get("max_retries", self.max_retries),
            timeout_seconds=worker.get("timeout", 7200),
            priority=worker.get("priority", 0),
        )

        # 更新状态
        if result.status == "completed":
            update_worker_status(worker_id, "completed", result=result.result, session_id=result.session_id)
        else:
            update_worker_status(worker_id, "failed", result=result.result, error=result.error, session_id=result.session_id)

        return result

    def execute_all(self, worker_ids: list[str], parallel: bool = True) -> dict[str, WorkerResult]:
        """
        执行所有 Workers

        True 并发：
          1. 按优先级排序（高→低）
          2. 所有 worker 同时 spawn（异步，不等待）
          3. 按优先级顺序 wait_for_result 全部完成

        顺序执行：
          1. spawn 一个
          2. wait_for_result 等待完成
          3. 重复

        Returns: {worker_id: WorkerResult, ...}
        """
        results = {}

        # 按优先级排序（高优先级在前），保持顺序稳定性
        def get_priority(wid: str) -> int:
            w = get_worker(wid)
            return w.get("priority", 0) if w else 0

        sorted_ids = sorted(worker_ids, key=get_priority, reverse=True)

        if parallel:
            # ─── 真正并发 ───
            # Phase 1: 同时 spawn 所有 workers（非阻塞，各自独立线程监控）
            spawned = []
            for wid in sorted_ids:
                worker = get_worker(wid)
                if not worker:
                    continue
                update_worker_status(wid, "running")
                self._pool.spawn(
                    worker_id=wid,
                    worker_type=worker["worker_type"],
                    goal=worker["goal"],
                    context=worker.get("context"),
                    max_retries=worker.get("max_retries", self.max_retries),
                    timeout_seconds=worker.get("timeout", 7200),
                    priority=worker.get("priority", 0),
                )
                spawned.append(wid)

            # Phase 2: 等待所有 workers 完成（所有 worker 同时运行，按完成顺序收集结果）
            pending = set(spawned)
            while pending:
                # 每次轮询所有 pending worker，把已完成的移出
                # 使用 list(pending) 快照避免迭代中修改 set 导致跳项
                completed_this_round = []
                for wid in list(pending):
                    result = self._pool.get_result(wid)
                    if result is not None:
                        update_worker_status(wid, result.status, result=result.result, error=result.error)
                        results[wid] = result
                        completed_this_round.append(wid)
                # 在循环外统一移除，避免在迭代中修改 set
                for wid in completed_this_round:
                    pending.discard(wid)
                if pending:
                    time.sleep(0.2)  # 缩短轮询间隔提升响应速度

        else:
            # ─── 顺序执行 ───
            for wid in sorted_ids:
                worker = get_worker(wid)
                if not worker:
                    continue
                update_worker_status(wid, "running")
                self._pool.spawn(
                    worker_id=wid,
                    worker_type=worker["worker_type"],
                    goal=worker["goal"],
                    context=worker.get("context"),
                    max_retries=worker.get("max_retries", self.max_retries),
                    timeout_seconds=worker.get("timeout", 7200),
                    priority=worker.get("priority", 0),
                )
                result = self._pool.wait_for_result(wid)
                if result:
                    update_worker_status(wid, result.status, result=result.result, error=result.error)
                    results[wid] = result

        return results

    def wait_all(self, worker_ids: list[str], poll_interval: float = 2.0) -> dict[str, WorkerResult]:
        """
        等待所有 Workers 完成（轮询方式）
        用于需要主动等待的场景
        """
        results = {}
        pending = set(worker_ids)

        while pending:
            for wid in list(pending):
                worker = get_worker(wid)
                if worker and worker["status"] in ("completed", "failed", "cancelled"):
                    results[wid] = WorkerResult(
                        worker_id=wid,
                        status=worker["status"],
                        result=worker.get("result"),
                        error=worker.get("error"),
                    )
                    pending.discard(wid)
                elif not worker:
                    pending.discard(wid)

            if pending:
                time.sleep(poll_interval)

        return results

    def retry_failed(self, worker_ids: list[str]) -> dict[str, WorkerResult]:
        """
        重试失败的 Workers
        """
        results = {}
        to_retry = []

        for wid in worker_ids:
            worker = get_worker(wid)
            if worker and worker["status"] == "failed":
                if worker["retry_count"] < worker.get("max_retries", self.max_retries):
                    increment_worker_retry(wid)
                    to_retry.append(wid)
                    print(f"[Harness] Retrying {wid} (attempt {worker['retry_count'] + 1})")
                else:
                    print(f"[Harness] {wid} exceeded max retries, skipping")
                    results[wid] = WorkerResult(worker_id=wid, status="failed", error="Max retries exceeded")
            elif worker and worker["status"] == "completed":
                results[wid] = WorkerResult(worker_id=wid, status="completed", result=worker.get("result"))

        if to_retry:
            retry_results = self.execute_all(to_retry)
            results.update(retry_results)

        return results

    def run(self, task_id: str, workers: list[dict], parallel: bool = True, auto_retry: bool = True) -> dict:
        """
        一站式执行：创建任务 → 分发 → 执行 → 重试 → 返回结果

        Args:
            task_id: 任务ID
            workers: worker 列表
            parallel: 是否并行
            auto_retry: 是否自动重试失败的 worker

        Returns:
            {
                "task_id": ...,
                "status": "completed"/"partial"/"failed",
                "worker_results": {...},
                "aggregated": ...
            }
        """
        # 1. 分发 workers
        worker_ids = self.dispatch(task_id, workers)

        # 2. 执行
        results = self.execute_all(worker_ids, parallel=parallel)

        # 3. 自动重试
        if auto_retry:
            failed_ids = [wid for wid, r in results.items() if r.status == "failed"]
            if failed_ids:
                print(f"[Harness] {len(failed_ids)} workers failed, auto-retrying...")
                retry_results = self.retry_failed(failed_ids)
                results.update(retry_results)

        # 4. 更新任务状态
        all_completed = all(r.status == "completed" for r in results.values())
        any_failed = any(r.status == "failed" for r in results.values())

        if all_completed:
            task_status = "completed"
        elif any_failed:
            task_status = "partial"
        else:
            task_status = "failed"

        # 汇总结果写入 task
        aggregated = self.aggregate_results(results)
        update_task_status(task_id, task_status, result=aggregated)

        return {
            "task_id": task_id,
            "status": task_status,
            "worker_results": {
                wid: {"status": r.status, "result": r.result, "error": r.error}
                for wid, r in results.items()
            },
            "aggregated": aggregated,
        }

    def aggregate_results(self, results: dict[str, WorkerResult]) -> str:
        """
        汇总所有 worker 结果为可读报告
        """
        lines = [f"## Worker 执行汇总\n"]
        lines.append(f"**总 Worker 数**: {len(results)}")
        lines.append(f"**成功**: {sum(1 for r in results.values() if r.status == 'completed')}")
        lines.append(f"**失败**: {sum(1 for r in results.values() if r.status == 'failed')}\n")

        for wid, r in results.items():
            status_icon = "✅" if r.status == "completed" else "❌"
            lines.append(f"### {status_icon} {wid}")
            if r.result:
                # 截断长输出
                result_preview = r.result[:1500] + "..." if len(r.result) > 1500 else r.result
                lines.append(f"```\n{result_preview}\n```")
            if r.error:
                lines.append(f"**错误**: {r.error}")
            lines.append("")

        return "\n".join(lines)

    def get_task_status(self, task_id: str) -> dict:
        """
        获取任务完整状态
        """
        task = get_task(task_id)
        if not task:
            return {"error": f"Task {task_id} not found"}

        workers = get_task_workers(task_id)
        stats = get_task_stats(task_id)

        return {
            "task": task,
            "workers": workers,
            "stats": stats,
        }

    def cancel_task(self, task_id: str):
        """
        取消任务（终止所有 worker）
        """
        workers = get_task_workers(task_id)
        for w in workers:
            if w["status"] in ("pending", "running"):
                self._pool.kill(w["id"])
                update_worker_status(w["id"], "cancelled")
        update_task_status(task_id, "cancelled")

    def shutdown(self):
        """
        关闭调度器（终止所有运行中的 worker）
        """
        self._pool.kill_all()
        self._executor.shutdown(wait=False)
        print("[Harness] Shutdown complete")

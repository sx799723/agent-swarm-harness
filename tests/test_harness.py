#!/usr/bin/env python3
"""MonoSwarm 测试套件 - Harness 单元测试（mock WorkerPool）"""

import sys
import os
import sqlite3
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

REAL_DB = os.path.expanduser("~/.hermes/agent-swarm/swarm.db")


def clean_db():
    conn = sqlite3.connect(REAL_DB)
    cur = conn.cursor()
    cur.execute("DELETE FROM ceo_assignments")
    cur.execute("DELETE FROM event_log")
    cur.execute("DELETE FROM workers")
    cur.execute("DELETE FROM tasks")
    conn.commit()
    conn.close()


def fresh_id():
    return f"hw-{uuid.uuid4().hex[:8]}"


class MockPool:
    def __init__(self):
        self.spawned = []

    def spawn(self, worker_id, worker_type, goal, context=None, max_retries=3):
        self.spawned.append({
            "worker_id": worker_id,
            "worker_type": worker_type,
            "goal": goal,
        })
        from harness import WorkerResult
        return WorkerResult(
            worker_id=worker_id,
            status="completed",
            result=f"Mock完成: {goal[:50]}",
            session_id=f"session-{worker_id}"
        )

    def kill(self, worker_id):
        pass

    def kill_all(self):
        pass


def test_harness_create_task():
    clean_db()
    from harness import AgentSwarmHarness
    from session_store import get_task
    
    h = AgentSwarmHarness()
    task_id = h.create_task("测试任务", "描述", "目标")
    
    assert task_id is not None
    task = get_task(task_id)
    assert task["title"] == "测试任务"
    assert task["status"] == "pending"
    print("  ✅ test_harness_create_task PASSED")


def test_harness_dispatch():
    clean_db()
    from harness import AgentSwarmHarness
    from session_store import get_worker
    
    h = AgentSwarmHarness()
    task_id = h.create_task("测试", "描述", "目标")
    
    w1 = fresh_id()
    w2 = fresh_id()
    workers = [
        {"id": w1, "worker_type": "code_worker", "goal": "写代码", "max_retries": 2},
        {"id": w2, "worker_type": "qa_worker", "goal": "测试"},
    ]
    
    from unittest.mock import patch
    with patch.object(h, '_pool', MockPool()):
        worker_ids = h.dispatch(task_id, workers)
    
    assert len(worker_ids) == 2
    assert w1 in worker_ids
    assert w2 in worker_ids
    
    w = get_worker(w1)
    assert w["worker_type"] == "code_worker"
    assert w["max_retries"] == 2
    print("  ✅ test_harness_dispatch PASSED")


def test_harness_execute_all_parallel():
    clean_db()
    from harness import AgentSwarmHarness
    from unittest.mock import patch
    
    h = AgentSwarmHarness()
    task_id = h.create_task("测试", "描述", "目标")
    
    w1 = fresh_id()
    w2 = fresh_id()
    workers = [
        {"id": w1, "worker_type": "code_worker", "goal": "任务1"},
        {"id": w2, "worker_type": "qa_worker", "goal": "任务2"},
    ]
    
    with patch.object(h, '_pool', MockPool()):
        worker_ids = h.dispatch(task_id, workers)
        results = h.execute_all(worker_ids, parallel=True)
    
    assert len(results) == 2
    assert results[w1].status == "completed"
    assert results[w2].status == "completed"
    print("  ✅ test_harness_execute_all_parallel PASSED")


def test_harness_execute_all_sequential():
    clean_db()
    from harness import AgentSwarmHarness
    from unittest.mock import patch
    
    h = AgentSwarmHarness()
    task_id = h.create_task("测试", "描述", "目标")
    
    w1 = fresh_id()
    w2 = fresh_id()
    workers = [
        {"id": w1, "worker_type": "code_worker", "goal": "任务1"},
        {"id": w2, "worker_type": "qa_worker", "goal": "任务2"},
    ]
    
    with patch.object(h, '_pool', MockPool()):
        worker_ids = h.dispatch(task_id, workers)
        results = h.execute_all(worker_ids, parallel=False)
    
    assert len(results) == 2
    assert results[w1].status == "completed"
    assert results[w2].status == "completed"
    print("  ✅ test_harness_execute_all_sequential PASSED")


def test_harness_run_full_flow():
    clean_db()
    from harness import AgentSwarmHarness
    from session_store import get_task
    from unittest.mock import patch
    
    h = AgentSwarmHarness()
    task_id = h.create_task("测试", "描述", "目标")
    
    w1 = fresh_id()
    w2 = fresh_id()
    workers = [
        {"id": w1, "worker_type": "code_worker", "goal": "写代码"},
        {"id": w2, "worker_type": "qa_worker", "goal": "测试代码"},
    ]
    
    with patch.object(h, '_pool', MockPool()):
        result = h.run(task_id, workers, parallel=True, auto_retry=False)
    
    assert result["task_id"] == task_id
    assert result["status"] == "completed"
    assert len(result["worker_results"]) == 2
    
    task = get_task(task_id)
    assert task["status"] == "completed"
    assert task["result"] is not None
    print("  ✅ test_harness_run_full_flow PASSED")


def test_harness_retry_failed():
    clean_db()
    from harness import AgentSwarmHarness, WorkerResult
    from session_store import create_worker, update_worker_status, get_worker
    from unittest.mock import patch
    
    h = AgentSwarmHarness()
    task_id = h.create_task("测试", "描述", "目标")
    wid = fresh_id()
    create_worker(task_id, wid, "code_worker", "写代码", max_retries=3)
    update_worker_status(wid, "failed", error="第一次失败")
    
    class FailingPool:
        def spawn(self, worker_id, worker_type, goal, context=None, max_retries=3):
            return WorkerResult(worker_id=worker_id, status="completed", result="重试成功")
        def kill(self, w): pass
        def kill_all(self): pass
    
    with patch.object(h, '_pool', FailingPool()):
        results = h.retry_failed([wid])
    
    assert results[wid].status == "completed"
    worker = get_worker(wid)
    assert worker["retry_count"] == 1
    print("  ✅ test_harness_retry_failed PASSED")


def test_harness_get_task_status():
    clean_db()
    from harness import AgentSwarmHarness
    from unittest.mock import patch
    
    h = AgentSwarmHarness()
    task_id = h.create_task("测试任务", "描述", "目标")
    
    wid = fresh_id()
    workers = [{"id": wid, "worker_type": "code_worker", "goal": "写代码"}]
    
    with patch.object(h, '_pool', MockPool()):
        h.dispatch(task_id, workers)
        status = h.get_task_status(task_id)
    
    assert status["task"]["title"] == "测试任务"
    assert "stats" in status
    assert len(status["workers"]) == 1
    print("  ✅ test_harness_get_task_status PASSED")


def test_harness_aggregate_results():
    clean_db()
    from harness import AgentSwarmHarness, WorkerResult
    
    h = AgentSwarmHarness()
    
    w1 = fresh_id()
    w2 = fresh_id()
    results = {
        w1: WorkerResult(w1, "completed", result="结果1"),
        w2: WorkerResult(w2, "failed", error="错误信息"),
    }
    
    aggregated = h.aggregate_results(results)
    assert w1 in aggregated
    assert w2 in aggregated
    print("  ✅ test_harness_aggregate_results PASSED")


if __name__ == "__main__":
    print(f"Using DB: {REAL_DB}")
    print("\n=== Harness Tests ===")
    test_harness_create_task()
    test_harness_dispatch()
    test_harness_execute_all_parallel()
    test_harness_execute_all_sequential()
    test_harness_run_full_flow()
    test_harness_retry_failed()
    test_harness_get_task_status()
    test_harness_aggregate_results()
    print("\n✅ All Harness Tests PASSED")

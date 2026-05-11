#!/usr/bin/env python3
"""MonoSwarm 性能测试 - 并发能力 + 响应时间"""

import sys
import os
import time
import sqlite3
import uuid
import concurrent.futures

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
    return f"perf-{uuid.uuid4().hex[:8]}"


def test_concurrent_task_creation():
    """并发创建任务测试"""
    clean_db()
    from session_store import create_task, get_all_tasks
    
    def create_n_tasks(n, prefix):
        ids = []
        for i in range(n):
            tid = create_task(f"ConcurrencyTest{i}", f"desc {i}", f"goal {i}")
            ids.append(tid)
        return ids
    
    start = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(create_n_tasks, 10, f"batch{i}") for i in range(10)]
        all_ids = []
        for f in concurrent.futures.as_completed(futures):
            all_ids.extend(f.result())
    
    elapsed = time.time() - start
    
    # 验证
    tasks = get_all_tasks()
    assert len(tasks) == 100, f"Expected 100 tasks, got {len(tasks)}"
    
    print(f"  ✅ test_concurrent_task_creation: 100 tasks created in {elapsed:.3f}s")
    print(f"     Throughput: {100/elapsed:.1f} tasks/sec")
    return elapsed


def test_concurrent_worker_creation():
    """并发创建 Worker 测试"""
    clean_db()
    from session_store import create_task, create_worker, get_task_workers
    
    task_id = create_task("PerfTest", "desc", "goal")
    
    def create_workers(n, wt):
        ids = []
        for i in range(n):
            wid = fresh_id()
            create_worker(task_id, wid, wt, f"goal {i}")
            ids.append(wid)
        return ids
    
    start = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(create_workers, 10, f"worker_type_{i}") for i in range(10)]
        all_ids = []
        for f in concurrent.futures.as_completed(futures):
            all_ids.extend(f.result())
    
    elapsed = time.time() - start
    
    workers = get_task_workers(task_id)
    assert len(workers) == 100, f"Expected 100 workers, got {len(workers)}"
    
    print(f"  ✅ test_concurrent_worker_creation: 100 workers created in {elapsed:.3f}s")
    print(f"     Throughput: {100/elapsed:.1f} workers/sec")
    return elapsed


def test_harness_parallel_execution():
    """Harness 并行执行测试"""
    clean_db()
    from harness import AgentSwarmHarness, WorkerResult
    from unittest.mock import patch
    import threading
    
    h = AgentSwarmHarness()
    task_id = h.create_task("ParallelTest", "desc", "goal")
    
    wids = [fresh_id() for _ in range(20)]
    workers = [{"id": wid, "worker_type": "code_worker", "goal": f"task {i}"} for i, wid in enumerate(wids)]
    
    class TimingPool:
        def __init__(self):
            self.results = {}
        
        def spawn(self, worker_id, worker_type, goal, context=None, max_retries=3):
            # 模拟100ms延迟
            time.sleep(0.1)
            return WorkerResult(worker_id=worker_id, status="completed", result=f"done: {goal[:20]}")
        
        def kill(self, w): pass
        def kill_all(self): pass
    
    with patch.object(h, '_pool', TimingPool()):
        start = time.time()
        results = h.execute_all(wids, parallel=True)
        elapsed = time.time() - start
    
    # 并行20个任务，每个100ms，理论上约1-2秒
    # 串行需要20*100ms=2秒
    assert all(r.status == "completed" for r in results.values())
    assert elapsed < 1.5, f"Parallel execution took {elapsed:.2f}s, expected < 1.5s"
    
    print(f"  ✅ test_harness_parallel_execution: 20 workers (100ms each) completed in {elapsed:.3f}s")
    print(f"     Speedup: ~{2.0/elapsed:.1f}x vs serial")
    return elapsed


def test_harness_sequential_execution():
    """Harness 顺序执行测试"""
    clean_db()
    from harness import AgentSwarmHarness, WorkerResult
    from unittest.mock import patch
    
    h = AgentSwarmHarness()
    task_id = h.create_task("SerialTest", "desc", "goal")
    
    wids = [fresh_id() for _ in range(10)]
    workers = [{"id": wid, "worker_type": "code_worker", "goal": f"task {i}"} for i, wid in enumerate(wids)]
    
    class TimingPool:
        def spawn(self, worker_id, worker_type, goal, context=None, max_retries=3):
            time.sleep(0.05)  # 50ms per worker
            return WorkerResult(worker_id=worker_id, status="completed", result=f"done: {goal[:20]}")
        def kill(self, w): pass
        def kill_all(self): pass
    
    with patch.object(h, '_pool', TimingPool()):
        start = time.time()
        results = h.execute_all(wids, parallel=False)
        elapsed = time.time() - start
    
    assert all(r.status == "completed" for r in results.values())
    # 10 * 50ms = 500ms minimum
    assert elapsed >= 0.45, f"Serial execution took {elapsed:.3f}s, expected >= 0.45s"
    
    print(f"  ✅ test_harness_sequential_execution: 10 workers (50ms each) completed in {elapsed:.3f}s")
    return elapsed


def test_response_time():
    """各核心操作响应时间测试"""
    clean_db()
    from session_store import create_task, get_task, update_task_status, create_worker, get_worker
    
    task_id = create_task("ResponseTest", "desc", "goal")
    
    # 1. get_task 响应时间
    times_get_task = []
    for _ in range(100):
        start = time.time()
        get_task(task_id)
        times_get_task.append(time.time() - start)
    
    avg_get_task = sum(times_get_task) / len(times_get_task) * 1000
    print(f"  ✅ get_task avg: {avg_get_task:.2f}ms (p95: {sorted(times_get_task)[95]*1000:.2f}ms)")
    
    # 2. create_worker 响应时间
    wid = fresh_id()
    start = time.time()
    for i in range(100):
        wid_i = f"{wid}-{i}"
        create_worker(task_id, wid_i, "code_worker", f"goal {i}")
    elapsed = time.time() - start
    print(f"  ✅ create_worker x100: {elapsed*10:.2f}ms avg per call")
    
    # 3. get_worker 响应时间
    start = time.time()
    for i in range(100):
        get_worker(f"{wid}-{i}")
    elapsed = time.time() - start
    print(f"  ✅ get_worker x100: {elapsed*10:.2f}ms avg per call")


if __name__ == "__main__":
    print(f"Using DB: {REAL_DB}")
    print("\n=== Performance Tests ===")
    test_concurrent_task_creation()
    test_concurrent_worker_creation()
    test_harness_parallel_execution()
    test_harness_sequential_execution()
    test_response_time()
    print("\n✅ All Performance Tests PASSED")

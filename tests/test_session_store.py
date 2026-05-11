#!/usr/bin/env python3
"""MonoSwarm 测试套件 - Session Store 单元测试（使用真实数据库，测试前清空）"""

import sys
import os
import sqlite3
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# 真实DB路径
REAL_DB = os.path.expanduser("~/.hermes/agent-swarm/swarm.db")


def clean_db():
    """清空所有表数据（保留表结构）"""
    conn = sqlite3.connect(REAL_DB)
    cur = conn.cursor()
    cur.execute("DELETE FROM ceo_assignments")
    cur.execute("DELETE FROM event_log")
    cur.execute("DELETE FROM workers")
    cur.execute("DELETE FROM tasks")
    conn.commit()
    conn.close()


import uuid as uuid_module

def fresh_id():
    return f"test-{uuid_module.uuid4().hex[:8]}"


def test_create_and_get_task():
    clean_db()
    from session_store import create_task, get_task
    
    task_id = create_task("测试任务", "描述", "目标")
    assert task_id is not None
    
    task = get_task(task_id)
    assert task is not None
    assert task["title"] == "测试任务"
    assert task["status"] == "pending"
    print("  ✅ test_create_and_get_task PASSED")


def test_update_task_status():
    clean_db()
    from session_store import create_task, get_task, update_task_status
    
    task_id = create_task("测试", "描述", "目标")
    
    update_task_status(task_id, "running")
    task = get_task(task_id)
    assert task["status"] == "running"
    
    update_task_status(task_id, "completed", result="成功结果")
    task = get_task(task_id)
    assert task["status"] == "completed"
    assert task["result"] == "成功结果"
    print("  ✅ test_update_task_status PASSED")


def test_create_and_get_worker():
    clean_db()
    from session_store import create_task, get_worker, create_worker
    
    task_id = create_task("测试", "描述", "目标")
    wid = fresh_id()
    worker_id = create_worker(task_id, wid, "code_worker", "写代码")
    assert worker_id == wid
    
    worker = get_worker(wid)
    assert worker is not None
    assert worker["worker_type"] == "code_worker"
    assert worker["status"] == "pending"
    assert worker["retry_count"] == 0
    print("  ✅ test_create_and_get_worker PASSED")


def test_update_worker_status():
    clean_db()
    from session_store import create_task, create_worker, get_worker, update_worker_status
    
    task_id = create_task("测试", "描述", "目标")
    wid = fresh_id()
    create_worker(task_id, wid, "code_worker", "写代码")
    
    update_worker_status(wid, "running")
    worker = get_worker(wid)
    assert worker["status"] == "running"
    
    update_worker_status(wid, "completed", result="代码写完了")
    worker = get_worker(wid)
    assert worker["status"] == "completed"
    assert worker["result"] == "代码写完了"
    print("  ✅ test_update_worker_status PASSED")


def test_increment_worker_retry():
    clean_db()
    from session_store import create_task, create_worker, get_worker, increment_worker_retry
    
    task_id = create_task("测试", "描述", "目标")
    wid = fresh_id()
    create_worker(task_id, wid, "code_worker", "写代码", max_retries=3)
    
    count = increment_worker_retry(wid)
    assert count == 1
    worker = get_worker(wid)
    assert worker["retry_count"] == 1
    assert worker["status"] == "pending"
    
    increment_worker_retry(wid)
    increment_worker_retry(wid)
    count = increment_worker_retry(wid)
    assert count == 4
    print("  ✅ test_increment_worker_retry PASSED")


def test_get_task_workers():
    clean_db()
    from session_store import create_task, create_worker, get_task_workers
    
    task_id = create_task("测试", "描述", "目标")
    w1 = fresh_id()
    w2 = fresh_id()
    create_worker(task_id, w1, "code_worker", "写代码")
    create_worker(task_id, w2, "qa_worker", "测试")
    
    workers = get_task_workers(task_id)
    assert len(workers) == 2
    print("  ✅ test_get_task_workers PASSED")


def test_get_all_tasks():
    clean_db()
    from session_store import create_task, get_all_tasks
    
    create_task("任务1", "d1", "g1")
    create_task("任务2", "d2", "g2")
    
    tasks = get_all_tasks()
    assert len(tasks) == 2
    print("  ✅ test_get_all_tasks PASSED")


def test_assign_worker_to_task():
    clean_db()
    from session_store import create_task, create_worker, assign_worker_to_task
    
    task_id = create_task("测试", "描述", "目标")
    wid = fresh_id()
    create_worker(task_id, wid, "code_worker", "写代码")
    assign_worker_to_task(task_id, wid)
    print("  ✅ test_assign_worker_to_task PASSED")


def test_is_all_workers_done():
    clean_db()
    from session_store import create_task, create_worker, update_worker_status, is_all_workers_done
    
    task_id = create_task("测试", "描述", "目标")
    w1 = fresh_id()
    w2 = fresh_id()
    create_worker(task_id, w1, "code_worker", "写代码")
    create_worker(task_id, w2, "qa_worker", "测试")
    
    assert is_all_workers_done(task_id) == False
    update_worker_status(w1, "completed")
    assert is_all_workers_done(task_id) == False
    update_worker_status(w2, "completed")
    assert is_all_workers_done(task_id) == True
    print("  ✅ test_is_all_workers_done PASSED")


def test_get_task_stats():
    clean_db()
    from session_store import create_task, create_worker, update_worker_status, get_task_stats
    
    task_id = create_task("测试", "描述", "目标")
    w1 = fresh_id()
    w2 = fresh_id()
    w3 = fresh_id()
    create_worker(task_id, w1, "code_worker", "写代码")
    create_worker(task_id, w2, "qa_worker", "测试")
    create_worker(task_id, w3, "doc_worker", "文档")
    
    update_worker_status(w1, "completed")
    update_worker_status(w2, "failed")
    
    stats = get_task_stats(task_id)
    assert stats.get("completed") == 1
    assert stats.get("failed") == 1
    assert stats.get("pending") == 1
    print("  ✅ test_get_task_stats PASSED")


def test_event_log():
    clean_db()
    from session_store import create_task, get_event_log
    
    task_id = create_task("测试", "描述", "目标")
    events = get_event_log(entity_type="task", entity_id=task_id)
    assert len(events) >= 1
    print("  ✅ test_event_log PASSED")


if __name__ == "__main__":
    print(f"Using DB: {REAL_DB}")
    print("\n=== Session Store Tests ===")
    test_create_and_get_task()
    test_update_task_status()
    test_create_and_get_worker()
    test_update_worker_status()
    test_increment_worker_retry()
    test_get_task_workers()
    test_get_all_tasks()
    test_assign_worker_to_task()
    test_is_all_workers_done()
    test_get_task_stats()
    test_event_log()
    print("\n✅ All Session Store Tests PASSED")

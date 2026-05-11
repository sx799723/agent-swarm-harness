#!/usr/bin/env python3
"""MonoSwarm 测试套件 - CEO Brain 单元测试（mock Harness）"""

import sys
import os
import sqlite3

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


from ceo_brain import CEOBrain, WORKER_TYPES
from worker_pool import WORKER_TYPE_SKILLS


def test_worker_types_defined():
    assert "code_worker" in WORKER_TYPES
    assert "qa_worker" in WORKER_TYPES
    assert "generic_worker" in WORKER_TYPES
    print("  ✅ test_worker_types_defined PASSED")


def test_worker_type_skills_defined():
    assert "code_worker" in WORKER_TYPE_SKILLS
    assert WORKER_TYPE_SKILLS["code_worker"] is not None
    print("  ✅ test_worker_type_skills_defined PASSED")


def test_ceo_decompose_code_task():
    clean_db()
    ceo = CEOBrain()
    result = ceo.decompose("写一个Python计算器程序")
    
    assert "subtasks" in result
    assert len(result["subtasks"]) >= 1
    assert result["subtasks"][0]["worker_type"] == "code_worker"
    print("  ✅ test_ceo_decompose_code_task PASSED")


def test_ceo_decompose_ppt_task():
    clean_db()
    ceo = CEOBrain()
    result = ceo.decompose("做一个项目汇报PPT")
    
    assert "subtasks" in result
    assert result["subtasks"][0]["worker_type"] == "ppt_worker"
    print("  ✅ test_ceo_decompose_ppt_task PASSED")


def test_ceo_decompose_test_task():
    clean_db()
    ceo = CEOBrain()
    result = ceo.decompose("测试登录功能")
    
    assert "subtasks" in result
    assert result["subtasks"][0]["worker_type"] == "qa_worker"
    print("  ✅ test_ceo_decompose_test_task PASSED")


def test_ceo_decompose_multiple_keywords():
    clean_db()
    ceo = CEOBrain()
    result = ceo.decompose("写一个API并写测试用例")
    
    worker_types = [st["worker_type"] for st in result["subtasks"]]
    assert "code_worker" in worker_types
    print("  ✅ test_ceo_decompose_multiple_keywords PASSED")


def test_ceo_decompose_unknown_task():
    clean_db()
    ceo = CEOBrain()
    result = ceo.decompose("随便做点什么")
    
    assert "subtasks" in result
    assert result["subtasks"][0]["worker_type"] == "generic_worker"
    print("  ✅ test_ceo_decompose_unknown_task PASSED")


def test_ceo_make_worker_goal():
    clean_db()
    ceo = CEOBrain()
    goal = ceo._make_worker_goal("写一个计算器")
    
    assert "write_file" in goal or "工具" in goal
    assert "写一个计算器" in goal
    print("  ✅ test_ceo_make_worker_goal PASSED")


def test_ceo_execute_with_mock():
    clean_db()
    from unittest.mock import patch
    
    ceo = CEOBrain()
    
    class MockHarness:
        def __init__(self):
            self.created_tasks = []
            self.ran_tasks = []
        
        def create_task(self, title, description, goal, parent_id=None):
            task_id = f"task-{len(self.created_tasks)}"
            self.created_tasks.append(task_id)
            return task_id
        
        def run(self, task_id, workers, parallel=True, auto_retry=True):
            self.ran_tasks.append(task_id)
            return {
                "task_id": task_id,
                "status": "completed",
                "worker_results": {
                    w["id"]: {"status": "completed", "result": "OK"}
                    for w in workers
                },
                "aggregated": "汇总"
            }
    
    mock_harness = MockHarness()
    with patch.object(ceo, 'harness', mock_harness):
        result = ceo.run_full_flow("写一个计算器", parallel=True)
    
    assert len(mock_harness.created_tasks) == 1
    assert mock_harness.ran_tasks[0] == mock_harness.created_tasks[0]
    assert "report" in result
    assert result["task_id"] is not None
    print("  ✅ test_ceo_execute_with_mock PASSED")


def test_ceo_execute_serial():
    clean_db()
    from unittest.mock import patch
    
    ceo = CEOBrain()
    
    class MockHarness:
        def create_task(self, title, description, goal, parent_id=None):
            return "mock-task"
        def run(self, task_id, workers, parallel=True, auto_retry=True):
            return {
                "task_id": task_id, "status": "completed",
                "worker_results": {}, "aggregated": "ok"
            }
    
    with patch.object(ceo, 'harness', MockHarness()):
        result = ceo.run_full_flow("写代码", parallel=False)
    
    assert result["task_id"] == "mock-task"
    print("  ✅ test_ceo_execute_serial PASSED")


if __name__ == "__main__":
    print(f"Using DB: {REAL_DB}")
    print("\n=== CEO Brain Tests ===")
    test_worker_types_defined()
    test_worker_type_skills_defined()
    test_ceo_decompose_code_task()
    test_ceo_decompose_ppt_task()
    test_ceo_decompose_test_task()
    test_ceo_decompose_multiple_keywords()
    test_ceo_decompose_unknown_task()
    test_ceo_make_worker_goal()
    test_ceo_execute_with_mock()
    test_ceo_execute_serial()
    print("\n✅ All CEO Brain Tests PASSED")

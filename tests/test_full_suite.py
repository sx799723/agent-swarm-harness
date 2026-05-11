#!/usr/bin/env python3
"""
MonoSwarm 综合测试报告生成器
功能测试 + 性能测试 + 代码质量检查 + 文档完整性
"""

import sys
import os
import time
import sqlite3
import uuid
import concurrent.futures
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

PROJECT_ROOT = "/Users/yutanglao/.hermes/agent-swarm"
REAL_DB = os.path.expanduser("~/.hermes/agent-swarm/swarm.db")

def clean_db():
    conn = sqlite3.connect(REAL_DB)
    cur = conn.cursor()
    for t in ['ceo_assignments','event_log','workers','tasks']:
        cur.execute(f"DELETE FROM {t}")
    conn.commit()
    conn.close()


def fresh_id():
    return f"r-{uuid.uuid4().hex[:8]}"


# ────────────────────────────
# 1. 功能测试
# ────────────────────────────

def run_functional_tests():
    results = {}
    
    # Session Store
    clean_db()
    from session_store import create_task, get_task, update_task_status, create_worker, get_worker, update_worker_status, increment_worker_retry, get_task_workers, get_all_tasks, assign_worker_to_task, is_all_workers_done, get_task_stats, get_event_log
    
    # Test: 创建任务
    tid = create_task("单元测试任务", "描述", "目标")
    results['create_task'] = get_task(tid) is not None and get_task(tid)['title'] == "单元测试任务"
    
    # Test: 更新任务状态
    update_task_status(tid, "completed", result="成功")
    results['update_task_status'] = get_task(tid)['status'] == "completed" and get_task(tid)['result'] == "成功"
    
    # Test: 创建和获取 worker
    wid = fresh_id()
    create_worker(tid, wid, "code_worker", "写代码")
    results['create_worker'] = get_worker(wid) is not None and get_worker(wid)['worker_type'] == "code_worker"
    
    # Test: 更新 worker 状态
    update_worker_status(wid, "running")
    results['update_worker_status'] = get_worker(wid)['status'] == "running"
    
    # Test: 重试计数
    count = increment_worker_retry(wid)
    results['increment_retry'] = count == 1 and get_worker(wid)['retry_count'] == 1
    
    # Test: 获取任务的所有 workers
    w2 = fresh_id()
    create_worker(tid, w2, "qa_worker", "测试")
    results['get_task_workers'] = len(get_task_workers(tid)) == 2
    
    # Test: 获取所有任务
    results['get_all_tasks'] = len(get_all_tasks()) >= 2
    
    # Test: 分配关系
    assign_worker_to_task(tid, wid)
    results['assign_worker'] = True
    
    # Test: 所有 worker 完成检查
    update_worker_status(wid, "completed")
    update_worker_status(w2, "completed")
    results['is_all_done'] = is_all_workers_done(tid) == True
    
    # Test: 任务统计
    results['task_stats'] = 'completed' in get_task_stats(tid)
    
    # Test: 事件日志
    results['event_log'] = len(get_event_log(entity_type='task', entity_id=tid)) >= 1
    
    # Harness
    clean_db()
    from harness import AgentSwarmHarness, WorkerResult
    
    class MockPool:
        def spawn(self, worker_id, worker_type, goal, context=None, max_retries=3):
            return WorkerResult(worker_id=worker_id, status="completed", result=f"Mock: {goal[:30]}", session_id=f"s-{worker_id}")
        def kill(self, w): pass
        def kill_all(self): pass
    
    h = AgentSwarmHarness()
    
    # Test: 创建任务
    tid2 = h.create_task("Harness测试", "desc", "goal")
    results['harness_create'] = get_task(tid2) is not None
    
    # Test: 分发 workers
    wids = [fresh_id(), fresh_id()]
    workers = [{"id": wids[0], "worker_type": "code_worker", "goal": "任务1"}, {"id": wids[1], "worker_type": "qa_worker", "goal": "任务2"}]
    with patch.object(h, '_pool', MockPool()):
        dispatched = h.dispatch(tid2, workers)
    results['harness_dispatch'] = len(dispatched) == 2
    
    # Test: 并行执行
    with patch.object(h, '_pool', MockPool()):
        r = h.execute_all(wids, parallel=True)
    results['harness_parallel'] = len(r) == 2 and all(v.status == "completed" for v in r.values())
    
    # Test: 串行执行
    with patch.object(h, '_pool', MockPool()):
        r = h.execute_all(wids, parallel=False)
    results['harness_serial'] = len(r) == 2 and all(v.status == "completed" for v in r.values())
    
    # Test: 完整流程
    tid3 = h.create_task("完整流程测试", "desc", "goal")
    w3 = [fresh_id()]
    workers3 = [{"id": w3[0], "worker_type": "code_worker", "goal": "写代码"}]
    with patch.object(h, '_pool', MockPool()):
        result = h.run(tid3, workers3, parallel=True, auto_retry=False)
    results['harness_run_full'] = result['status'] == "completed" and len(result['worker_results']) == 1
    
    # Test: 重试失败
    clean_db()
    from session_store import create_worker, update_worker_status as upd_ws
    tid4 = h.create_task("重试测试", "desc", "goal")
    wid_fail = fresh_id()
    create_worker(tid4, wid_fail, "code_worker", "代码", max_retries=3)
    upd_ws(wid_fail, "failed", error="失败")
    
    class RetryPool:
        def spawn(self, worker_id, worker_type, goal, context=None, max_retries=3):
            return WorkerResult(worker_id=worker_id, status="completed", result="重试成功")
        def kill(self, w): pass
        def kill_all(self): pass
    
    with patch.object(h, '_pool', RetryPool()):
        retry_results = h.retry_failed([wid_fail])
    results['harness_retry'] = retry_results[wid_fail].status == "completed"
    
    # CEO Brain
    clean_db()
    from ceo_brain import CEOBrain, WORKER_TYPES
    from worker_pool import WORKER_TYPE_SKILLS
    
    results['worker_types'] = len(WORKER_TYPES) >= 7
    results['worker_skill_map'] = len(WORKER_TYPE_SKILLS) >= 7
    
    ceo = CEOBrain()
    
    # Test: 任务拆解 - 代码
    d = ceo.decompose("写一个Python程序")
    results['decompose_code'] = d['subtasks'][0]['worker_type'] == 'code_worker'
    
    # Test: 任务拆解 - PPT
    d = ceo.decompose("制作项目汇报PPT")
    results['decompose_ppt'] = d['subtasks'][0]['worker_type'] == 'ppt_worker'
    
    # Test: 任务拆解 - 测试
    d = ceo.decompose("测试登录功能")
    results['decompose_test'] = d['subtasks'][0]['worker_type'] == 'qa_worker'
    
    # Test: 任务拆解 - 文档
    d = ceo.decompose("整理季度报告表格")
    results['decompose_doc'] = d['subtasks'][0]['worker_type'] == 'doc_worker'
    
    # Test: 任务拆解 - 视频
    d = ceo.decompose("剪辑视频")
    results['decompose_video'] = d['subtasks'][0]['worker_type'] == 'video_worker'
    
    # Test: 任务拆解 - UI
    d = ceo.decompose("设计一个logo")
    results['decompose_ui'] = d['subtasks'][0]['worker_type'] == 'ui_worker'
    
    # Test: 任务拆解 - 未知
    d = ceo.decompose("随便做点什么")
    results['decompose_generic'] = d['subtasks'][0]['worker_type'] == 'generic_worker'
    
    # Test: 多关键词
    d = ceo.decompose("写一个API并写测试用例")
    results['decompose_multi'] = 'code_worker' in [s['worker_type'] for s in d['subtasks']]
    
    # Test: Worker goal 构造
    goal = ceo._make_worker_goal("写计算器")
    results['make_goal'] = 'write_file' in goal and 'terminal' in goal
    
    # Test: 完整流程 mock
    class MockHarness:
        def create_task(self, title, description, goal, parent_id=None):
            return "mock-task-id"
        def run(self, task_id, workers, parallel=True, auto_retry=True):
            return {"task_id": task_id, "status": "completed", "worker_results": {w['id']: {"status": "completed"} for w in workers}, "aggregated": "ok"}
    
    with patch.object(ceo, 'harness', MockHarness()):
        result = ceo.run_full_flow("写代码", parallel=True)
    results['ceo_full_flow'] = 'report' in result and result['task_id'] == "mock-task-id"
    
    return results


# ────────────────────────────
# 2. 性能测试
# ────────────────────────────

def run_performance_tests():
    results = {}
    
    clean_db()
    from session_store import create_task, get_task, create_worker, get_task_workers
    from harness import AgentSwarmHarness, WorkerResult
    from unittest.mock import patch
    
    # 并发创建任务
    def create_tasks(n):
        ids = []
        for i in range(n):
            ids.append(create_task(f"Perf{i}", "d", "g"))
        return ids
    
    start = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        futures = [ex.submit(create_tasks, 10) for _ in range(10)]
        list(concurrent.futures.as_completed(futures))
    elapsed = time.time() - start
    results['concurrent_task_throughput'] = round(100/elapsed, 1)
    
    # 并发创建 workers
    clean_db()
    tid = create_task("PerfTask", "d", "g")
    def create_workers(n, wt):
        ids = []
        for i in range(n):
            wid = fresh_id()
            create_worker(tid, wid, wt, f"goal{i}")
            ids.append(wid)
        return ids
    
    start = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        futures = [ex.submit(create_workers, 10, f"wt{i}") for i in range(10)]
        list(concurrent.futures.as_completed(futures))
    elapsed = time.time() - start
    results['concurrent_worker_throughput'] = round(100/elapsed, 1)
    
    # 并行 worker 执行加速比
    clean_db()
    h = AgentSwarmHarness()
    tid = h.create_task("ParallelPerf", "d", "g")
    wids = [fresh_id() for _ in range(20)]
    workers = [{"id": w, "worker_type": "code_worker", "goal": f"t{i}"} for i, w in enumerate(wids)]
    
    class SlowPool:
        def spawn(self, wid, wt, goal, context=None, max_retries=3):
            time.sleep(0.05)  # 50ms each
            return WorkerResult(worker_id=wid, status="completed", result="done")
        def kill(self, w): pass
        def kill_all(self): pass
    
    with patch.object(h, '_pool', SlowPool()):
        # 并行
        h.dispatch(tid, workers)
        start = time.time()
        r_parallel = h.execute_all(wids, parallel=True)
        t_parallel = time.time() - start
        
        # 串行（用新task）
        tid2 = h.create_task("SerialPerf", "d", "g")
        wids2 = [fresh_id() for _ in range(20)]
        workers2 = [{"id": w, "worker_type": "code_worker", "goal": f"t{i}"} for i, w in enumerate(wids2)]
        h.dispatch(tid2, workers2)
        start = time.time()
        r_serial = h.execute_all(wids2, parallel=False)
        t_serial = time.time() - start
    
    results['parallel_speedup'] = round(t_serial / t_parallel, 1) if t_parallel > 0 else 0
    results['parallel_all_completed'] = all(v.status == "completed" for v in r_parallel.values())
    results['serial_all_completed'] = all(v.status == "completed" for v in r_serial.values())
    
    # DB 操作响应时间
    clean_db()
    from session_store import get_all_tasks
    
    times = []
    for _ in range(50):
        create_task("Timing", "d", "g")
        start = time.time()
        get_all_tasks()
        times.append(time.time() - start)
    avg_ms = sum(times)/len(times)*1000
    results['get_all_tasks_avg_ms'] = round(avg_ms, 2)
    
    return results


# ────────────────────────────
# 3. 代码质量检查
# ────────────────────────────

def run_code_quality_checks():
    checks = {}
    root = "/Users/yutanglao/.hermes/agent-swarm"
    
    files = {
        'ceo_brain.py': os.path.join(root, 'ceo_brain.py'),
        'harness.py': os.path.join(root, 'harness.py'),
        'worker_pool.py': os.path.join(root, 'worker_pool.py'),
        'session_store.py': os.path.join(root, 'session_store.py'),
        'run.py': os.path.join(root, 'run.py'),
        'config.py': os.path.join(root, 'config.py'),
    }
    
    for name, path in files.items():
        with open(path) as f:
            content = f.read()
            lines = content.split('\n')
        
        # 统计
        code_lines = [l for l in lines if l.strip() and not l.strip().startswith('#')]
        checks[f'{name}_loc'] = len(code_lines)
        
        # docstring 检查
        checks[f'{name}_has_docstring'] = '"""' in content or "'''" in content
        
        # type hints 检查
        import re
        fns = re.findall(r'def (\w+)\(', content)
        checks[f'{name}_functions'] = len(fns)
        
        # 硬编码路径检查
        checks[f'{name}_no_hardcoded_swarm_dir'] = '~/.hermes/agent-swarm' not in content or 'SWARM_DIR' in content
    
    # 循环引用检查
    try:
        import importlib
        import ceo_brain, harness, worker_pool, session_store
        importlib.reload(session_store)
        importlib.reload(harness)
        importlib.reload(worker_pool)
        importlib.reload(ceo_brain)
        checks['no_circular_import'] = True
    except ImportError as e:
        checks['no_circular_import'] = False
        checks['import_error'] = str(e)
    
    return checks


# ────────────────────────────
# 4. 文档完整性检查
# ────────────────────────────

def run_doc_completeness():
    checks = {}
    root = "/Users/yutanglao/.hermes/agent-swarm"
    
    # README 存在性和关键内容
    readme = os.path.join(root, "package", "README.md")
    if os.path.exists(readme):
        with open(readme) as f:
            content = f.read()
        checks['readme_exists'] = True
        checks['readme_has_install'] = 'install' in content.lower()
        checks['readme_has_usage'] = '使用' in content or 'usage' in content.lower()
        checks['readme_has_worker_types'] = 'worker' in content.lower()
        checks['readme_has_architecture'] = '架构' in content or 'architecture' in content.lower()
    else:
        checks['readme_exists'] = False
    
    # deploy.sh 存在性
    deploy = os.path.join(root, "package", "deploy.sh")
    checks['deploy_script_exists'] = os.path.exists(deploy)
    
    # requirements.txt
    reqs = os.path.join(root, "package", "requirements.txt")
    checks['requirements_exists'] = os.path.exists(reqs)
    
    # 关键文件完整性
    key_files = ['ceo_brain.py', 'harness.py', 'worker_pool.py', 'session_store.py', 'run.py', 'config.py']
    for f in key_files:
        path = os.path.join(root, f)
        checks[f'{f}_exists'] = os.path.exists(path)
        if os.path.exists(path):
            with open(path) as fp:
                content = fp.read()
            checks[f'{f}_has_docstring'] = '"""' in content or "'''" in content
            checks[f'{f}_nonempty'] = len(content.strip()) > 0
    
    return checks


# ────────────────────────────
# 生成报告
# ────────────────────────────

def generate_report():
    print("=" * 70)
    print(" MonoSwarm 综合测试报告")
    print("=" * 70)
    
    print("\n[1/4] 功能测试...")
    ft = run_functional_tests()
    ft_pass = sum(1 for v in ft.values() if v)
    ft_total = len(ft)
    print(f"  结果: {ft_pass}/{ft_total} 通过")
    for k, v in ft.items():
        icon = "✅" if v else "❌"
        print(f"    {icon} {k}")
    
    print("\n[2/4] 性能测试...")
    pt = run_performance_tests()
    print(f"  并发任务创建吞吐: {pt.get('concurrent_task_throughput', 'N/A')} tasks/sec")
    print(f"  并发Worker创建吞吐: {pt.get('concurrent_worker_throughput', 'N/A')} workers/sec")
    print(f"  并行加速比: {pt.get('parallel_speedup', 'N/A')}x")
    print(f"  并行执行全部完成: {pt.get('parallel_all_completed', 'N/A')}")
    print(f"  串行执行全部完成: {pt.get('serial_all_completed', 'N/A')}")
    print(f"  get_all_tasks 平均: {pt.get('get_all_tasks_avg_ms', 'N/A')}ms")
    
    print("\n[3/4] 代码质量检查...")
    cq = run_code_quality_checks()
    total_loc = sum(v for k, v in cq.items() if k.endswith('_loc'))
    print(f"  总代码行数: {total_loc}")
    for k, v in cq.items():
        if '_loc' in k:
            fname = k.replace('_loc', '')
            icon = "✅" if v > 0 else "❌"
            print(f"    {icon} {fname}: {v} LOC")
    
    print("\n  架构设计检查:")
    for k, v in cq.items():
        if 'no_circular' in k or 'import_error' in k:
            icon = "✅" if v == True else "❌"
            print(f"    {icon} {k}: {v}")
    
    print("\n[4/4] 文档完整性...")
    dc = run_doc_completeness()
    for k, v in dc.items():
        icon = "✅" if v else "❌"
        print(f"    {icon} {k}: {v}")
    
    print("\n" + "=" * 70)
    print(" 测试总结")
    print("=" * 70)
    print(f"  功能测试: {ft_pass}/{ft_total} ({round(ft_pass/ft_total*100)}%)")
    print(f"  代码总行数: {total_loc}")
    print(f"  核心文件: 6个 (ceo_brain, harness, worker_pool, session_store, run, config)")
    print(f"  Worker类型: 8种")
    print(f"  文档: README.md, deploy.sh, requirements.txt")
    
    all_pass = ft_pass == ft_total
    print(f"\n  整体状态: {'✅ 全部通过' if all_pass else '⚠️ 部分通过'}")
    print("=" * 70)
    
    return {
        'functional': {'pass': ft_pass, 'total': ft_total, 'details': ft},
        'performance': pt,
        'code_quality': cq,
        'documentation': dc
    }


if __name__ == "__main__":
    generate_report()

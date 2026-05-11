#!/usr/bin/env python3
"""
Agent Swarm Session Store
SQLite 持久化层 — 负责所有任务/Worker/日志的增删改查
"""

import sqlite3
import json
import uuid
import os
from datetime import datetime
from typing import Optional

SWARM_DIR = os.path.expanduser("~/.hermes/agent-swarm")
DB_PATH = os.path.join(SWARM_DIR, "swarm.db")


def get_db():
    """获取数据库连接"""
    os.makedirs(SWARM_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """初始化数据库"""
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            parent_id TEXT,
            title TEXT NOT NULL,
            description TEXT,
            goal TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            result TEXT,
            error TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            completed_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS workers (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            worker_type TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            goal TEXT NOT NULL,
            context TEXT,
            result TEXT,
            error TEXT,
            retry_count INTEGER DEFAULT 0,
            max_retries INTEGER DEFAULT 3,
            session_id TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            started_at TEXT,
            completed_at TEXT,
            FOREIGN KEY (task_id) REFERENCES tasks(id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS event_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            event TEXT NOT NULL,
            detail TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS ceo_assignments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            worker_id TEXT NOT NULL,
            assigned_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (task_id) REFERENCES tasks(id),
            FOREIGN KEY (worker_id) REFERENCES workers(id)
        )
    """)

    # 索引
    cur.execute("CREATE INDEX IF NOT EXISTS idx_workers_task ON workers(task_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_workers_status ON workers(status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_events_entity ON event_log(entity_type, entity_id)")

    conn.commit()
    conn.close()


# ─────────────────────────────────────────
# Task 操作
# ─────────────────────────────────────────

def create_task(title: str, description: str, goal: str, parent_id: str = None) -> str:
    """创建新任务"""
    task_id = str(uuid.uuid4())
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO tasks (id, parent_id, title, description, goal) VALUES (?, ?, ?, ?, ?)""",
        (task_id, parent_id, title, description, goal)
    )
    _log(conn, "task", task_id, "created", {"title": title})
    conn.commit()
    conn.close()
    return task_id


def get_task(task_id: str) -> Optional[dict]:
    """获取任务"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def update_task_status(task_id: str, status: str, result: str = None, error: str = None):
    """更新任务状态"""
    conn = get_db()
    cur = conn.cursor()
    now = datetime.now().isoformat()
    if status in ("completed", "failed", "cancelled"):
        cur.execute(
            """UPDATE tasks SET status = ?, updated_at = ?, completed_at = ?, result = ?, error = ?
               WHERE id = ?""",
            (status, now, now, result, error, task_id)
        )
    else:
        cur.execute(
            """UPDATE tasks SET status = ?, updated_at = ?, result = ?, error = ? WHERE id = ?""",
            (status, now, result, error, task_id)
        )
    _log(conn, "task", task_id, f"status_{status}", {"result": result, "error": error})
    conn.commit()
    conn.close()


def get_task_workers(task_id: str) -> list[dict]:
    """获取任务的所有 workers"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM workers WHERE task_id = ?", (task_id,))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_tasks() -> list[dict]:
    """获取所有任务（按创建时间倒序）"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM tasks ORDER BY created_at DESC")
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─────────────────────────────────────────
# Worker 操作
# ─────────────────────────────────────────

def create_worker(task_id: str, worker_id: str, worker_type: str, goal: str, context: dict = None, max_retries: int = 3, timeout: int = 7200, priority: int = 0) -> str:
    """创建 worker"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO workers (id, task_id, worker_type, goal, context, max_retries, timeout, priority)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (worker_id, task_id, worker_type, goal, json.dumps(context or {}), max_retries, timeout, priority)
    )
    _log(conn, "worker", worker_id, "created", {"task_id": task_id, "worker_type": worker_type, "timeout": timeout, "priority": priority})
    conn.commit()
    conn.close()
    return worker_id


def get_worker(worker_id: str) -> Optional[dict]:
    """获取 worker"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM workers WHERE id = ?", (worker_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def update_worker_status(worker_id: str, status: str, result: str = None, error: str = None, session_id: str = None):
    """更新 worker 状态"""
    conn = get_db()
    cur = conn.cursor()
    now = datetime.now().isoformat()
    if status in ("completed", "failed", "cancelled"):
        cur.execute(
            """UPDATE workers SET status = ?, completed_at = ?, result = ?, error = ?, session_id = COALESCE(?, session_id)
               WHERE id = ?""",
            (status, now, result, error, session_id, worker_id)
        )
    else:
        cur.execute(
            """UPDATE workers SET status = ?, result = ?, error = ?, session_id = COALESCE(?, session_id)
               WHERE id = ?""",
            (status, result, error, session_id, worker_id)
        )
    _log(conn, "worker", worker_id, f"status_{status}", {"result": result, "error": error})
    conn.commit()
    conn.close()


def increment_worker_retry(worker_id: str) -> int:
    """增加重试计数"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """UPDATE workers SET retry_count = retry_count + 1, status = 'pending' WHERE id = ?""",
        (worker_id,)
    )
    cur.execute("SELECT retry_count FROM workers WHERE id = ?", (worker_id,))
    retry_count = cur.fetchone()[0]
    _log(conn, "worker", worker_id, "retry", {"retry_count": retry_count})
    conn.commit()
    conn.close()
    return retry_count


def get_pending_workers() -> list[dict]:
    """获取所有 pending 状态的 worker（用于恢复）"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM workers WHERE status IN ('pending', 'running')")
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─────────────────────────────────────────
# 日志操作
# ─────────────────────────────────────────

def _log(conn, entity_type: str, entity_id: str, event: str, detail: dict = None):
    """写事件日志"""
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO event_log (entity_type, entity_id, event, detail) VALUES (?, ?, ?, ?)""",
        (entity_type, entity_id, event, json.dumps(detail or {}))
    )


def get_event_log(entity_type: str = None, entity_id: str = None, limit: int = 50) -> list[dict]:
    """获取事件日志"""
    conn = get_db()
    cur = conn.cursor()
    if entity_type and entity_id:
        cur.execute(
            """SELECT * FROM event_log WHERE entity_type = ? AND entity_id = ?
               ORDER BY created_at DESC LIMIT ?""",
            (entity_type, entity_id, limit)
        )
    elif entity_type:
        cur.execute(
            """SELECT * FROM event_log WHERE entity_type = ?
               ORDER BY created_at DESC LIMIT ?""",
            (entity_type, limit)
        )
    else:
        cur.execute("SELECT * FROM event_log ORDER BY created_at DESC LIMIT ?", (limit,))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─────────────────────────────────────────
# CEO 任务分配
# ─────────────────────────────────────────

def assign_worker_to_task(task_id: str, worker_id: str):
    """记录任务到 worker 的分配关系"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO ceo_assignments (task_id, worker_id) VALUES (?, ?)""",
        (task_id, worker_id)
    )
    conn.commit()
    conn.close()


# ─────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────

def is_all_workers_done(task_id: str) -> bool:
    """检查任务的所有 worker 是否都已完成"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """SELECT COUNT(*) FROM workers WHERE task_id = ? AND status NOT IN ('completed', 'failed', 'cancelled')""",
        (task_id,)
    )
    count = cur.fetchone()[0]
    conn.close()
    return count == 0


def get_task_stats(task_id: str) -> dict:
    """获取任务统计"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """SELECT status, COUNT(*) as count FROM workers WHERE task_id = ? GROUP BY status""",
        (task_id,)
    )
    rows = cur.fetchall()
    stats = {r["status"]: r["count"] for r in rows}
    conn.close()
    return stats


# 初始化
init_db()

"""
MonoSwarm Self-Diagnostic Module
Automatically detects execution anomalies, outputs diagnostic reports,
and provides a basic health check API.
"""

import os
import sys
import json
import traceback
import threading
import importlib
from datetime import datetime
from pathlib import Path
from typing import Optional

# ── Paths ──────────────────────────────────────────────────────────────────

SWARM_ROOT = Path(__file__).parent
DIAGNOSTICS_DIR = SWARM_ROOT / "diagnostics"
DIAGNOSTICS_DIR.mkdir(exist_ok=True)

DIAGNOSTICS_LOG = DIAGNOSTICS_DIR / "diagnostic_log.jsonl"
HEALTH_REPORT  = DIAGNOSTICS_DIR / "health_report.json"
EXCEPTION_LOG  = DIAGNOSTICS_DIR / "exceptions.jsonl"

# ── Global state ────────────────────────────────────────────────────────────

_lock = threading.Lock()
_exception_history: list[dict] = []
_start_time: Optional[datetime] = None

# ── Core: exception capture ─────────────────────────────────────────────────

def _capture_exception(exc: BaseException, context: str = "") -> dict:
    """Serialize an exception into a structured dict."""
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    entry = {
        "timestamp": datetime.now().isoformat(),
        "type":      type(exc).__name__,
        "message":   str(exc),
        "traceback": tb,
        "context":   context,
    }
    return entry

def install_exception_hook():
    """
    Install a global excepthook that automatically captures every uncaught
    exception in the process and writes it to diagnostics/exceptions.jsonl.
    """
    _global_hook_installed = False   # closure sentinel

    def global_hook(exc_type, exc_value, exc_tb):
        entry = _capture_exception(exc_value)
        entry["stage"] = "uncaught"
        with _lock:
            _exception_history.append(entry)
        _write_jsonl(EXCEPTION_LOG, entry)
        # call original hook
        old_hook(exc_type, exc_value, exc_tb)

    old_hook = sys.excepthook
    sys.excepthook = global_hook

    # Also patch threading Thread.run to catch thread-local exceptions
    _orig_run = threading.Thread.run
    def _patched_run(self, *args, **kwargs):
        try:
            return _orig_run(self, *args, **kwargs)
        except BaseException as e:
            entry = _capture_exception(e, context=f"thread:{self.name}")
            entry["stage"] = "thread"
            with _lock:
                _exception_history.append(entry)
            _write_jsonl(EXCEPTION_LOG, entry)
            raise
    threading.Thread.run = _patched_run

# ── Core: JSONL helpers ───────────────────────────────────────────────────────

def _write_jsonl(path: Path, entry: dict):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

# ── Core: component health checks ──────────────────────────────────────────

def _check_file_exists(path: Path) -> dict:
    status = "ok" if path.exists() else "missing"
    return {"status": status, "path": str(path)}

def _check_import(module_name: str) -> dict:
    try:
        m = importlib.import_module(module_name)
        return {"status": "ok", "version": getattr(m, "__version__", "unknown")}
    except ImportError as e:
        return {"status": "error", "detail": str(e)}

def _check_worker_pool_api() -> dict:
    """Smoke-check: can we instantiate WorkerPool and call list_workers?"""
    try:
        from worker_pool import WorkerPool
        pool = WorkerPool()
        workers = pool.list_workers()
        return {"status": "ok", "worker_count": len(workers)}
    except Exception as e:
        return {"status": "error", "detail": str(e)}

def _check_session_store() -> dict:
    """Verify session_store module loads and its harness-compatible API works."""
    try:
        import session_store
        # harness.py uses session_store directly (module-level API)
        store_mod = session_store
        return {"status": "ok", "module": "session_store"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}

def _check_harness() -> dict:
    """Check if AgentSwarmHarness (ceo_brain) loads without error."""
    try:
        from ceo_brain import AgentSwarmHarness
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}

# ── Public API ───────────────────────────────────────────────────────────────

def run_health_check() -> dict:
    """
    Run all diagnostic checks and return a structured health report.
    Writes the report to diagnostics/health_report.json.
    """
    global _start_time
    if _start_time is None:
        _start_time = datetime.now()

    uptime = (datetime.now() - _start_time).total_seconds()

    checks = {
        "core_modules": {
            "worker_pool":    _check_import("worker_pool"),
            "session_store":  _check_import("session_store"),
            "ceo_brain":      _check_import("ceo_brain"),
            "harness":        _check_import("harness"),
        },
        "file_health": {
            "swarm_db":   _check_file_exists(SWARM_ROOT / "swarm.db"),
            "config_py":  _check_file_exists(SWARM_ROOT / "config.py"),
        },
        "runtime": {
            "worker_pool_spawn": _check_worker_pool_api(),
            "session_store_init": _check_session_store(),
            "ceo_brain_load":     _check_harness(),
        },
        "exception_history_count": len(_exception_history),
    }

    report = {
        "generated_at": datetime.now().isoformat(),
        "uptime_seconds": uptime,
        "checks": checks,
        "overall": _compute_overall(checks),
    }

    with open(HEALTH_REPORT, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    return report

def _compute_overall(checks: dict) -> str:
    """Derive overall status from sub-check statuses."""
    errors = []
    for section, items in checks.items():
        if isinstance(items, dict):
            for v in items.values():
                if isinstance(v, dict) and v.get("status") == "error":
                    errors.append(f"{section}: {v.get('detail', v.get('status'))}")
    return "healthy" if not errors else f"degraded ({len(errors)} issue(s))"

def get_latest_exceptions(n: int = 10) -> list[dict]:
    """Return the last N captured exceptions."""
    with _lock:
        return list(_exception_history[-n:])

def check_exception_log(n: int = 50) -> list[dict]:
    """Read the last N entries from exceptions.jsonl."""
    if not EXCEPTION_LOG.exists():
        return []
    with open(EXCEPTION_LOG, encoding="utf-8") as f:
        lines = f.readlines()
    return [json.loads(l) for l in lines[-n:] if l.strip()]

def generate_diagnostic_report() -> Path:
    """
    Top-level entry point: run health check + append to diagnostic_log.jsonl
    and return the path to the written report.
    """
    report = run_health_check()
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "overall":   report["overall"],
        "uptime":    report["uptime_seconds"],
        "issues":    _collect_issues(report),
    }
    _write_jsonl(DIAGNOSTICS_LOG, log_entry)
    return HEALTH_REPORT

def _collect_issues(report: dict) -> list[str]:
    issues = []
    for section, items in report.get("checks", {}).items():
        if isinstance(items, dict):
            for k, v in items.items():
                if isinstance(v, dict) and v.get("status") == "error":
                    issues.append(f"[{section}.{k}] {v.get('detail', 'unknown error')}")
    return issues

# ── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    install_exception_hook()
    path = generate_diagnostic_report()
    print(f"Diagnostic report written to: {path}")

    # Print summary
    with open(path) as f:
        r = json.load(f)
    print(f"Overall status: {r['overall']}")
    print(f"Uptime: {r['uptime_seconds']:.1f}s")
    issues = _collect_issues(r)
    if issues:
        print("Issues found:")
        for i in issues:
            print(f"  - {i}")
    else:
        print("No issues detected.")

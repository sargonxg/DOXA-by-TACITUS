"""Explicit local execution budgets, without changing analytical methods.

The Docker memory limit is the aggregate process-tree enforcement boundary.
Native Python uses bounded workers/parser limits; a budget is not a guarantee
that unrelated programs or a separately hosted model fit into that budget.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
import threading
from typing import Mapping

MIB = 1024 * 1024
PROFILES = {
    "low-memory": {"target_host_memory_mib": 8192, "application_budget_mib": 4096,
                   "parser_memory_mib": 1536, "parser_slots": 1, "heavy_job_slots": 1,
                   "cpu_threads": 2, "cpu_batch_size": 2},
    "standard": {"target_host_memory_mib": 16384, "application_budget_mib": 8192,
                 "parser_memory_mib": 4096, "parser_slots": 2, "heavy_job_slots": 2,
                 "cpu_threads": 4, "cpu_batch_size": 8},
    "server": {"target_host_memory_mib": None, "application_budget_mib": None,
               "parser_memory_mib": 4096, "parser_slots": 2, "heavy_job_slots": 2,
               "cpu_threads": 4, "cpu_batch_size": 8},
}


def local_limits(environ: Mapping[str, str] | None = None, *, cloud_state: bool | None = None) -> dict:
    """Return operational settings only; no model choice, download or network."""
    environment = os.environ if environ is None else environ
    remote = bool(environment.get("DOXA_CLOUD_STATE_URL")) if cloud_state is None else cloud_state
    name = environment.get("DOXA_LOCAL_PROFILE", "server" if remote else "low-memory")
    if name not in PROFILES:
        raise ValueError("DOXA_LOCAL_PROFILE must be low-memory, standard or server")
    return {"name": name, **PROFILES[name], "automatic_downloads": False,
            "automatic_cloud_fallback": False, "large_model_required": False,
            "memory_boundary": "Docker limits the application process tree; native budgets require operator enforcement; separate model servers are outside this budget."}


def apply_local_environment_defaults(environ=None, *, cloud_state: bool | None = None) -> dict:
    """Set only absent execution controls, preserving explicit operator choices."""
    environment = os.environ if environ is None else environ
    limits = local_limits(environment, cloud_state=cloud_state)
    defaults = {"DOXA_PARSER_MEMORY_MB": limits["parser_memory_mib"],
                "DOXA_PARSER_SLOTS": limits["parser_slots"],
                "DOXA_CPU_THREADS": limits["cpu_threads"],
                "DOXA_CPU_BATCH_SIZE": limits["cpu_batch_size"],
                "DOXA_OLLAMA_NUM_THREADS": limits["cpu_threads"],
                "DOXA_LOCAL_HEAVY_SLOTS": limits["heavy_job_slots"]}
    for key, value in defaults.items():
        environment.setdefault(key, str(value))
    effective_limits(environment, cloud_state=cloud_state)
    return limits


def effective_limits(environ=None, *, cloud_state: bool | None = None) -> dict:
    """Report validated operator overrides without presenting them as defaults."""
    environment = os.environ if environ is None else environ
    profile = local_limits(environment, cloud_state=cloud_state)
    settings = {"parser_memory_mib": ("DOXA_PARSER_MEMORY_MB", 256, 16384),
                "parser_slots": ("DOXA_PARSER_SLOTS", 1, 8),
                "heavy_job_slots": ("DOXA_LOCAL_HEAVY_SLOTS", 1, 8),
                "cpu_threads": ("DOXA_CPU_THREADS", 1, 32),
                "cpu_batch_size": ("DOXA_CPU_BATCH_SIZE", 1, 64)}
    values = {}
    overrides = []
    for name, (key, minimum, maximum) in settings.items():
        try:
            value = int(environment.get(key, profile[name]))
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be an integer between {minimum} and {maximum}") from None
        if not minimum <= value <= maximum:
            raise ValueError(f"{key} must be an integer between {minimum} and {maximum}")
        values[name] = value
        if value != profile[name]:
            overrides.append(key)
    return {**values, "operator_overrides": overrides,
            "profile_defaults_retained": not overrides}


def configured_slots(kind: str, environ=None) -> int:
    environment = os.environ if environ is None else environ
    limits = local_limits(environment)
    key = {"parser": "DOXA_PARSER_SLOTS", "heavy": "DOXA_LOCAL_HEAVY_SLOTS"}.get(kind)
    if key is None:
        raise ValueError("Unknown local worker kind")
    default = limits["parser_slots" if kind == "parser" else "heavy_job_slots"]
    try:
        value = int(environment.get(key, str(default)))
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be an integer between 1 and 8") from None
    if not 1 <= value <= 8:
        raise ValueError(f"{key} must be an integer between 1 and 8")
    return value


_gate_lock = threading.Lock()
_gates = {}
_held = threading.local()


@contextmanager
def heavy_operation():
    """Hold the slot in the actual synchronous worker, including cancellation.

    Call inside the worker thread, not around an awaiter that may be cancelled.
    Reentrant calls within one thread do not deadlock. This is a single-server-
    process scheduler; Docker provides the separate aggregate memory boundary.
    """
    if getattr(_held, "depth", 0):
        _held.depth += 1
        try:
            yield
        finally:
            _held.depth -= 1
        return
    slots = configured_slots("heavy")
    with _gate_lock:
        gate = _gates.setdefault(slots, threading.BoundedSemaphore(slots))
    with gate:
        _held.depth = 1
        try:
            yield
        finally:
            _held.depth = 0


def run_heavy(function, *args, **kwargs):
    with heavy_operation():
        return function(*args, **kwargs)

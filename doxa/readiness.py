"""Read-only, content-free readiness diagnostics; never load or download models."""
from __future__ import annotations

import ctypes.util
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import sys

from . import __version__
from .local_profile import MIB, effective_limits, local_limits


def _module(name):
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, AttributeError):
        return False


def _memory():
    """Best-effort host/cgroup metadata, not a model memory requirement."""
    result = {"host_total_mib": None, "available_mib": None, "container_limit_mib": None}
    try:
        result["host_total_mib"] = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // MIB
        result["available_mib"] = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_AVPHYS_PAGES") // MIB
        information = Path("/proc/meminfo")
        if information.is_file():
            values = dict(line.split(":", 1) for line in information.read_text().splitlines() if ":" in line)
            if "MemAvailable" in values:
                result["available_mib"] = int(values["MemAvailable"].split()[0]) // 1024
    except (OSError, ValueError, AttributeError):
        pass
    try:
        maximum = Path("/sys/fs/cgroup/memory.max").read_text().strip()
        if maximum != "max":
            limit = int(maximum)
            current = int(Path("/sys/fs/cgroup/memory.current").read_text().strip())
            result["container_limit_mib"] = limit // MIB
            available = max(0, limit - current) // MIB
            result["available_mib"] = min(result["available_mib"], available) if result["available_mib"] is not None else available
    except (OSError, ValueError):
        pass
    return result


def _storage(path):
    """Inspect the nearest existing parent; never create a directory or probe file."""
    candidate = Path(path).expanduser()
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    try:
        information = candidate.stat()
        writable = bool(information.st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)) and os.access(candidate, os.W_OK | os.X_OK)
        free = shutil.disk_usage(candidate).free
        return {"writable": writable and candidate.is_dir(), "free_mib": free // MIB,
                "check": "permissions_and_free_space_only", "write_probe_performed": False}
    except (OSError, ValueError):
        return {"writable": False, "free_mib": None, "check": "unavailable", "write_probe_performed": False}


def _models():
    from .cpu_models import CPUModelError, verify_installation
    from .model_registry import get_model_profile
    models = []
    for key in ("minilm", "deberta"):
        profile = get_model_profile(key)
        try:
            verify_installation(key, hashes=False)
            installed, condition = True, "identity_and_sizes_checked"
        except CPUModelError as error:
            installed, condition = False, error.code
        models.append({"id": key, "role": profile["role"], "model": profile["model"],
                       "revision": profile["revision"], "installed": installed,
                       "installation_check": condition, "artifact_hashes_verified": False,
                       "quality_measured": False})
    return models


def readiness(data_dir, *, environ=None, include_models=True):
    environment = os.environ if environ is None else environ
    profile = local_limits(environment)
    execution = effective_limits(environment)
    storage = _storage(data_dir)
    memory = _memory()
    checks = []

    def add(identity, status, message, action=None):
        checks.append({"id": identity, "status": status, "message": message,
                       **({"action": action} if action else {})})

    add("execution_profile", "ok" if execution["profile_defaults_retained"] else "warning",
        f"The {profile['name']} execution defaults are active." if execution["profile_defaults_retained"] else "Operator resource overrides differ from the selected profile.",
        None if execution["profile_defaults_retained"] else "Inspect effective_execution values. An overridden configuration is not the default 8 GB operating profile.")

    add("storage_permissions", "ok" if storage["writable"] else "error",
        "Application data storage appears writable." if storage["writable"] else "Application data storage is not writable.",
        None if storage["writable"] else "Choose a writable data directory or correct its owner and permissions; DOXA has not changed it.")
    free = storage["free_mib"]
    add("storage_headroom", "unknown" if free is None else "warning" if free < 2048 else "ok",
        "Free storage could not be read." if free is None else f"{free} MiB free on the application data filesystem.",
        "Small demonstrations need less storage than complete archives. Allow at least 30 GB for the full corpus, local weights, history and export/build headroom; large work needs a scoped estimate.")
    available = memory["available_mib"]
    add("memory_headroom", "unknown" if available is None else "warning" if available < 1536 else "ok",
        "Available memory could not be measured." if available is None else f"{available} MiB currently available; separate model servers need additional memory.",
        "Use the low-memory profile, close other heavy applications, and run OCR, indexing and generation sequentially.")
    pdf = {name: _module(name) for name in ("pdfminer", "pypdf", "reportlab", "weasyprint")}
    add("pdf_generation", "ok" if pdf["reportlab"] else "error",
        "ReportLab is installed; this diagnostic has not rendered a PDF." if pdf["reportlab"] else "The PDF report dependency is missing.",
        None if pdf["reportlab"] else "Reinstall the DOXA wheel with its dependencies, or use the documented Docker image.")
    pdf_text = pdf["pdfminer"] and pdf["pypdf"]
    add("pdf_extraction", "ok" if pdf_text else "warning",
        "The permissive PDF extraction dependencies are installed." if pdf_text else "One or more default PDF extraction dependencies are missing.",
        None if pdf_text else "Reinstall the current DOXA wheel and dependencies. TXT/Markdown intake remains available.")
    native = {name: bool(ctypes.util.find_library(name)) for name in ("pango-1.0", "gobject-2.0", "harfbuzz")}
    ocr = environment.get("DOXA_ENABLE_OCR", "0").lower() in {"1", "true", "yes"}
    ocr_models = Path(environment["DOXA_DOCLING_ARTIFACTS"]).is_dir() if environment.get("DOXA_DOCLING_ARTIFACTS") else False
    ocr_available = ocr and _module("docling") and ocr_models
    add("ocr", "ok" if ocr_available else "optional",
        "OCR is enabled with local artifacts; a processing test is still required." if ocr_available else "OCR is not ready; scanned pages will not silently count as analyzed.",
        None if ocr_available else "Install the OCR extra, explicitly prefetch its models, configure DOXA_DOCLING_ARTIFACTS and enable OCR, or supply a text-readable document.")
    static = Path(__file__).with_name("static") / "index.html"
    source_ui = Path(__file__).resolve().parent.parent / "frontend/dist/index.html"
    ui = static.is_file() or source_ui.is_file()
    add("browser_ui", "ok" if ui else "error", "A compiled browser interface is available." if ui else "The compiled browser interface is missing.",
        None if ui else "Install the published self-contained wheel, or build frontend once in a source checkout.")
    ui_manifest = (static if static.is_file() else source_ui).with_name("doxa-build.json")
    ui_version = None
    try:
        if ui_manifest.is_file() and ui_manifest.stat().st_size <= 4096:
            declared = json.loads(ui_manifest.read_text()).get("version")
            if isinstance(declared, str) and len(declared) <= 80:
                ui_version = declared
    except (OSError, ValueError, AttributeError):
        pass
    ui_match = ui_version == __version__ if ui_version else None
    add("ui_version", "ok" if ui_match is True else "warning" if ui_match is False else "unknown",
        "Browser build and API versions agree." if ui_match is True else "Browser build and API versions differ." if ui_match is False else "Browser build version metadata is unavailable.",
        None if ui_match is True else "Install the matching release wheel or rebuild the interface from this source version; do not mix application versions.")
    cpu_dependencies = {name: _module(name) for name in ("torch", "transformers", "tokenizers", "safetensors", "numpy", "onnxruntime")}
    models = _models() if include_models else []
    try:
        installed = importlib.metadata.version("doxa-by-tacitus")
    except importlib.metadata.PackageNotFoundError:
        installed = None
    if installed and installed != __version__:
        add("installed_version", "warning", "Installed package metadata differs from the imported application version.",
            "Reinstall the intended wheel or editable source package in this Python environment.")
    return {"schema_version": "doxa-readiness-1", "version": __version__, "installed_distribution_version": installed,
            "frontend_version": ui_version, "api_ui_version_match": ui_match,
            "ok": not any(check["status"] == "error" for check in checks), "profile": profile,
            "effective_execution": execution,
            "platform": sys.platform, "storage": storage, "memory": memory, "checks": checks,
            "pdf_dependencies": pdf, "optional_native_pdf_libraries": native, "cpu_dependencies": cpu_dependencies,
            "models": models, "model_check_scope": "local metadata only; no runtime loaded or inference requested",
            "provider_probe_performed": False, "network_requests": 0, "model_requests": 0,
            "automatic_downloads": False, "source_content_included": False, "private_paths_included": False,
            "notice": "Readiness is software configuration, not interpretation accuracy, guaranteed memory fit or full PDF/OCR correctness."}


def register_readiness_api(app, store_factory):
    """The main app's managed-access allowlist must authorize these read routes."""
    @app.get("/api/system/readiness")
    def system_readiness():
        store = store_factory()
        return {**readiness(store.root), "storage_backend": "remote" if getattr(store, "remote", "") else "native"}

    @app.get("/api/system/starter-pack")
    def system_starter_pack():
        from .starter_pack import starter_pack
        return starter_pack()

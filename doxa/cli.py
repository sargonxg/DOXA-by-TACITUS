import argparse
import json
import os
from pathlib import Path
import stat


def load_private_environment(filename):
    """Load explicitly selected operator secrets before importing the API."""
    path = Path(filename).expanduser().resolve(strict=True)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise ValueError("The pilot environment must be a private regular file (mode 0600)")
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise ValueError("The pilot environment must belong to the current operator")
    from dotenv import load_dotenv
    load_dotenv(path, override=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="DOXA by TACITUS research workbench")
    parser.add_argument("command", nargs="?", choices=["serve", "doctor"], default="serve")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--env-file", help="Explicit private pilot.env file; never served to the browser")
    parser.add_argument("--local-profile", choices=["low-memory", "standard", "server"],
                        help="Explicit operational profile; no model selection or download")
    parser.add_argument("--json", action="store_true", help="Machine-readable doctor output")
    parser.add_argument("--laptop", action="store_true", help="Check the local Ollama model with doxa doctor")
    args = parser.parse_args(argv)
    if args.command != "doctor" and args.laptop:
        parser.error("--laptop is available with doxa doctor")
    if args.env_file:
        try:
            load_private_environment(args.env_file)
        except (OSError, ValueError) as error:
            parser.error(str(error))
    if args.command == "doctor":
        from .readiness import readiness
        environment = dict(os.environ)
        if args.local_profile:
            environment["DOXA_LOCAL_PROFILE"] = args.local_profile
        try:
            report = readiness(args.data_dir, environ=environment)
        except ValueError as error:
            parser.error(str(error))
        if args.laptop:
            import asyncio
            from .laptop import ENDPOINT, MODEL, PROFILE
            from .providers import OllamaProvider
            if environment.get("DOXA_CLOUD_STATE_URL"):
                health = {"ok": False, "details": "The laptop preset needs native workspace storage."}
            else:
                health = asyncio.run(OllamaProvider({"model": MODEL, "execution_profile": PROFILE}).health())
                report.update(provider_probe_performed=True, network_requests=1)
            report["laptop"] = {"endpoint": ENDPOINT, "model": MODEL, "ok": health["ok"],
                                "revision": health.get("revision"), "quantization": health.get("quantization")}
            model_check = {"status": "ok" if health["ok"] else "error", "message": health["details"], "id": "laptop_model"}
            if not health["ok"] and report["provider_probe_performed"]:
                model_check["action"] = f"Start Ollama locally and run ollama pull {MODEL}."
            report["checks"].append(model_check)
            report["ok"] = report["ok"] and health["ok"]
            report["notice"] = ("The laptop check queries local model metadata on loopback. "
                                "It performs no inference or model download.")
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print(f"DOXA {report['version']} · {report['profile']['name']} · configuration check")
            for check in report["checks"]:
                print(f"{check['status'].upper()}: {check['message']}")
                if check.get("action"):
                    print(f"  {check['action']}")
            print(report["notice"])
        return 0 if report["ok"] else 1
    if args.json:
        parser.error("--json is available with doxa doctor")
    if args.local_profile:
        os.environ["DOXA_LOCAL_PROFILE"] = args.local_profile
    from .local_profile import apply_local_environment_defaults
    try:
        apply_local_environment_defaults()
    except ValueError as error:
        parser.error(str(error))
    os.environ["DOXA_DATA_DIR"] = args.data_dir
    import uvicorn
    uvicorn.run("doxa.api:app", host=args.host, port=args.port)


if __name__ == "__main__":
    raise SystemExit(main())

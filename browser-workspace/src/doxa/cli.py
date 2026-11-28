"""Command-line entry points for DOXA."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(prog="doxa")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor")
    data = commands.add_parser("data").add_subparsers(dest="operation", required=True)
    for name in ("build", "validate"):
        data.add_parser(name)
    for name in ("train", "evaluate"):
        command = commands.add_parser(name)
        command.add_argument("--config", type=Path, default=Path("configs") / f"{'baseline' if name == 'train' else 'evaluation'}.yaml")
    commands.add_parser("export-browser")
    release = commands.add_parser("release").add_subparsers(dest="operation", required=True)
    for name in ("stage", "check", "verify", "collection"):
        release.add_parser(name)
    publish_command = release.add_parser("publish")
    publish_command.add_argument("--namespace", required=True)
    publish_command.add_argument("--public", action="store_true")
    publish_command.add_argument("--budget-usd", type=int, required=True)
    args = parser.parse_args()
    root = Path.cwd()
    try:
        output = run(root, args)
        if output is not None:
            print(json.dumps(output, indent=2))
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"doxa: {error}\n")


def run(root, args):
    from doxa import release
    if args.command == "doctor":
        return release.doctor()
    if args.command == "release":
        if args.operation == "publish":
            return release.publish(root, args.namespace, args.public, args.budget_usd)
        if args.operation == "stage":
            release.stage(root)
            return release.check(root)
        return getattr(release, args.operation)(root)
    if args.command == "data":
        subprocess.run([sys.executable, "-m", "doxa.data", args.operation], cwd=root, check=True)
        return
    if args.command == "export-browser":
        subprocess.run(["npm.cmd" if os.name == "nt" else "npm", "run", "build"], cwd=root / "web", check=True)
        return
    config_path = args.config
    if not config_path.is_file():
        raise FileNotFoundError(config_path)
    environment = os.environ | {"OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"}
    call = [sys.executable, "-m", "doxa.models.baseline", "--config", str(config_path)]
    if args.command == "evaluate":
        call.append("--evaluate")
    subprocess.run(call, cwd=root, check=True, env=environment)


if __name__ == "__main__":
    main()

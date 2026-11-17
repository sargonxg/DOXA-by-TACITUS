#!/usr/bin/env python3
"""Provision private native-pilot credentials without printing secrets.

Keep this directory outside a Git checkout and outside DOXA's data directory.
This helper does not start servers, create users at an identity provider, or
rotate an existing identity-vault encryption key. It supports POSIX private-file
permissions; managed pilot deployment needs its own TLS and backup policy.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import stat
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import dotenv_values  # noqa: E402

from doxa.access import AccessControl, ROLE_PERMISSIONS, issue_token, token_hash  # noqa: E402

FILES = {"config": "access.json", "credentials": "credentials.json", "environment": "pilot.env"}
JOURNAL = ".pending-access-update.json"
MAX_BYTES = 512 * 1024
IDENTITY = re.compile(r"[A-Za-z0-9@._:-]{1,128}\Z")


def _json(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _inside_checkout(path: Path) -> bool:
    return any((parent / ".git").exists() for parent in (path, *path.parents))


def _directory(value) -> Path:
    path = Path(value).expanduser().absolute()
    if any(char in str(path) for char in ("\n", "\r", "\0")):
        raise ValueError("Private configuration paths cannot contain control characters")
    if os.name != "posix":
        raise ValueError("This managed-pilot helper requires POSIX private-file permissions")
    if path.is_symlink():
        raise ValueError("Private configuration directory cannot be a symlink")
    # Normalize non-existing `missing/../checkout` paths before checking their
    # placement; mkdir(parents=True) must not turn a failed ancestor lookup into
    # a later write inside a repository.
    path = path.resolve(strict=False)
    if _inside_checkout(path):
        raise ValueError("Choose a private configuration directory outside Git checkouts")
    return path


def _read_private(path: Path) -> bytes:
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES or info.st_mode & 0o077:
            raise ValueError("Provisioning files must be regular private files no larger than 512 KiB")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            value = stream.read(MAX_BYTES + 1)
        if len(value) > MAX_BYTES:
            raise ValueError("Provisioning file exceeds its size limit")
        return value
    except OSError as error:
        raise ValueError("A required private provisioning file is unavailable") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _sync_directory(directory: Path):
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _exclusive(path: Path, value: bytes):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())


def _atomic(path: Path, value: bytes):
    if path.is_symlink():
        raise ValueError("Private provisioning files cannot be symlinks")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".provision-", delete=False) as stream:
            temporary = Path(stream.name)
            os.chmod(temporary, 0o600)
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        temporary = None
        _sync_directory(path.parent)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@contextmanager
def _locked(directory: Path):
    import fcntl
    if not directory.is_dir() or directory.stat().st_mode & 0o077:
        raise ValueError("Private configuration directory must have mode 0700")
    descriptor = os.open(directory / ".configure.lock", os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if os.fstat(descriptor).st_mode & 0o077:
            raise ValueError("Provisioning lock must be private")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def _check_config(config: dict, directory: Path):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".validate-access-", delete=False) as stream:
            temporary = Path(stream.name)
            os.chmod(temporary, 0o600)
            stream.write(_json(config))
        AccessControl("managed", str(temporary)).configuration()
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _bundle(directory: Path):
    if (directory / JOURNAL).exists():
        raise ValueError("An interrupted access update requires the explicit recover --confirm command")
    raw = {name: _read_private(directory / filename) for name, filename in FILES.items()}
    config, credentials = json.loads(raw["config"]), json.loads(raw["credentials"])
    _check_config(config, directory)
    _check_credentials(config, credentials)
    environment = dotenv_values(directory / FILES["environment"])
    if (environment.get("DOXA_ACCESS_MODE") != "managed" or
            environment.get("DOXA_ACCESS_CONFIG_FILE") != str(directory / FILES["config"])):
        raise ValueError("Private environment does not point to this managed configuration")
    try:
        key = base64.b64decode(environment.get("DOXA_PRIVACY_VAULT_KEY", ""), validate=True)
    except (TypeError, ValueError) as error:
        raise ValueError("Private environment contains an invalid identity-vault key") from error
    if len(key) != 32:
        raise ValueError("Private environment contains an invalid identity-vault key")
    return config, credentials, raw


def _check_credentials(config: dict, credentials: dict):
    if not isinstance(credentials, dict) or credentials.get("version") != 1 or not isinstance(credentials.get("tokens"), dict):
        raise ValueError("Private credential delivery file is invalid")
    tokens = credentials["tokens"]
    if set(tokens) != {user["id"] for user in config["users"]} or any(
        not isinstance(tokens[user["id"]], str) or token_hash(tokens[user["id"]]) != user["token_sha256"]
        for user in config["users"]
    ):
        raise ValueError("Credential delivery file and active configuration are inconsistent")


def _receipt(directory: Path, *, operation: str, user_id: str | None = None) -> dict:
    return {"operation": operation, "directory": str(directory), "user_id": user_id,
            "files": {name: str(directory / filename) for name, filename in FILES.items()},
            "secrets_printed": False,
            "notice": "Read and deliver individual tokens privately from credentials.json. Keep all provisioning files outside the repository and data backups; protect the vault key separately."}


def initialize(directory: str | Path, owner_id: str, *, kind: str = "organization") -> dict:
    """Create fresh private files; never overwrite an existing directory/key."""
    directory = _directory(directory)
    if not isinstance(owner_id, str) or not IDENTITY.fullmatch(owner_id):
        raise ValueError("Owner identity must be a bounded ASCII identifier")
    if kind not in {"organization", "personal"}:
        raise ValueError("Pilot workspace kind must be organization or personal")
    if directory.exists():
        raise ValueError("Private directory already exists; initialization never overwrites credentials or encryption keys")
    directory.mkdir(parents=True, mode=0o700)
    with _locked(directory):
        token = issue_token()
        config = {"version": 1, "session_secret": secrets.token_urlsafe(48), "users": [
            {"id": owner_id, "role": "owner", "token_sha256": token_hash(token), "project_ids": [],
             "all_projects": True, "permissions": []}]}
        _check_config(config, directory)
        credentials = {"version": 1, "tokens": {owner_id: token}}
        key = base64.b64encode(os.urandom(32)).decode("ascii")
        environment = ("# Private DOXA pilot environment. Never commit or display its contents.\n"
                       "DOXA_ACCESS_MODE=managed\n"
                       f"DOXA_PILOT_WORKSPACE_KIND={kind}\n"
                       f"DOXA_ACCESS_CONFIG_FILE={shlex.quote(str(directory / FILES['config']))}\n"
                       f"DOXA_PRIVACY_VAULT_KEY={key}\n").encode()
        # Publish the valid access configuration only after delivery material
        # exists. Interrupted initialization is preserved for operator recovery.
        _exclusive(directory / FILES["environment"], environment)
        _exclusive(directory / FILES["credentials"], _json(credentials))
        _exclusive(directory / FILES["config"], _json(config))
        _sync_directory(directory)
    return _receipt(directory, operation="initialized", user_id=owner_id)


def _commit_bundle(directory: Path, config: dict, credentials: dict, original: dict[str, bytes]):
    _check_config(config, directory)
    _check_credentials(config, credentials)
    journal = {"version": 1, "original_sha256": {name: _hash(original[name]) for name in ("config", "credentials")},
               "environment_sha256": _hash(original["environment"]),
               "config": config, "credentials": credentials}
    _exclusive(directory / JOURNAL, _json(journal))
    _sync_directory(directory)
    _recover(directory)


def _recover(directory: Path):
    journal = json.loads(_read_private(directory / JOURNAL))
    if (not isinstance(journal, dict) or journal.get("version") != 1 or
            set(journal.get("original_sha256", {})) != {"config", "credentials"}):
        raise ValueError("Pending access update is invalid")
    _check_config(journal["config"], directory)
    _check_credentials(journal["config"], journal["credentials"])
    if _hash(_read_private(directory / FILES["environment"])) != journal.get("environment_sha256"):
        raise ValueError("Private environment changed during the pending update; refusing to alter access files")
    desired = {"config": _json(journal["config"]), "credentials": _json(journal["credentials"])}
    for name, payload in desired.items():
        current = _read_private(directory / FILES[name])
        if _hash(current) not in {journal["original_sha256"][name], _hash(payload)}:
            raise ValueError("Provisioning files changed outside the pending update; refusing to overwrite them")
    for name in ("credentials", "config"):
        _atomic(directory / FILES[name], desired[name])
    (directory / JOURNAL).unlink()
    _sync_directory(directory)


def add_user(directory: str | Path, user_id: str, *, role: str = "analyst", project_ids=(),
             all_projects: bool = False, identity_reveal: bool = False, replace_user: bool = False) -> dict:
    directory = _directory(directory)
    if not isinstance(user_id, str) or not IDENTITY.fullmatch(user_id) or role not in ROLE_PERMISSIONS:
        raise ValueError("Supply a valid user identity and role")
    if any(not isinstance(value, bool) for value in (all_projects, identity_reveal, replace_user)):
        raise ValueError("Grant and replacement flags must be explicit booleans")
    if isinstance(project_ids, str):
        raise ValueError("Project grants must contain explicit project identities")
    project_ids = list(project_ids)
    if len(project_ids) > 2000 or any(not isinstance(item, str) or not IDENTITY.fullmatch(item) for item in project_ids):
        raise ValueError("Project grants must contain explicit project identities")
    if all_projects and role not in {"owner", "admin"}:
        raise ValueError("Only an owner or admin may receive explicit all-projects access")
    with _locked(directory):
        config, credentials, raw = _bundle(directory)
        existing = next((index for index, user in enumerate(config["users"]) if user["id"] == user_id), None)
        if existing is not None and not replace_user:
            raise ValueError("User already exists; --replace-user is required to replace grants and revoke its token")
        token = issue_token()
        user = {"id": user_id, "role": role, "token_sha256": token_hash(token), "project_ids": sorted(set(project_ids)),
                "all_projects": bool(all_projects), "permissions": ["identity_reveal"] if identity_reveal else []}
        if existing is None:
            config["users"].append(user)
        else:
            config["users"][existing] = user
        credentials["tokens"][user_id] = token
        _commit_bundle(directory, config, credentials, raw)
    return _receipt(directory, operation="user_replaced" if existing is not None else "user_added", user_id=user_id)


def rotate_token(directory: str | Path, user_id: str, *, confirm: bool = False) -> dict:
    if not confirm:
        raise ValueError("Token rotation requires --confirm; this revokes that user's existing sessions")
    directory = _directory(directory)
    with _locked(directory):
        config, credentials, raw = _bundle(directory)
        user = next((user for user in config["users"] if user["id"] == user_id), None)
        if user is None:
            raise ValueError("Managed user was not found")
        token = issue_token()
        user["token_sha256"] = token_hash(token)
        credentials["tokens"][user_id] = token
        _commit_bundle(directory, config, credentials, raw)
    return _receipt(directory, operation="token_rotated", user_id=user_id)


def recover(directory: str | Path, *, confirm: bool = False) -> dict:
    if not confirm:
        raise ValueError("Recovery requires --confirm to complete the previously requested access update")
    directory = _directory(directory)
    with _locked(directory):
        if not (directory / JOURNAL).exists():
            raise ValueError("No interrupted access update exists")
        _recover(directory)
        _bundle(directory)
    return _receipt(directory, operation="access_update_recovered")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    initial = commands.add_parser("init", help="Create a new private configuration directory")
    initial.add_argument("--directory", required=True)
    initial.add_argument("--owner", required=True)
    initial.add_argument("--kind", choices=("organization", "personal"), default="organization")
    add = commands.add_parser("add-user", help="Provision explicit role/project grants and a new individual token")
    add.add_argument("--directory", required=True)
    add.add_argument("--user", required=True)
    add.add_argument("--role", choices=sorted(ROLE_PERMISSIONS), default="analyst")
    add.add_argument("--project-id", action="append", default=[])
    add.add_argument("--all-projects", action="store_true")
    add.add_argument("--identity-reveal", action="store_true")
    add.add_argument("--replace-user", action="store_true")
    rotate = commands.add_parser("rotate-token", help="Explicitly revoke a user's token and sessions, preserving vault keys")
    rotate.add_argument("--directory", required=True)
    rotate.add_argument("--user", required=True)
    rotate.add_argument("--confirm", action="store_true")
    recovery = commands.add_parser("recover", help="Complete an interrupted access-file update")
    recovery.add_argument("--directory", required=True)
    recovery.add_argument("--confirm", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            result = initialize(args.directory, args.owner, kind=args.kind)
        elif args.command == "add-user":
            result = add_user(args.directory, args.user, role=args.role, project_ids=args.project_id,
                              all_projects=args.all_projects, identity_reveal=args.identity_reveal,
                              replace_user=args.replace_user)
        elif args.command == "rotate-token":
            result = rotate_token(args.directory, args.user, confirm=args.confirm)
        else:
            result = recover(args.directory, confirm=args.confirm)
    except (ValueError, OSError, KeyError, TypeError) as error:
        # Validation errors deliberately do not include the secret file content.
        parser.exit(2, f"Private provisioning failed: {error}\n")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

import importlib.util
import json
import os
from pathlib import Path
import stat

from dotenv import dotenv_values
import pytest

from doxa.access import AccessControl, token_hash


spec = importlib.util.spec_from_file_location("configure_pilot", Path(__file__).resolve().parents[1] / "scripts" / "configure_pilot.py")
configure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(configure)


@pytest.fixture(autouse=True)
def isolate_outer_repository_discovery(tmp_path, monkeypatch):
    # The managed execution harness may checkpoint /tmp itself in Git. Keep
    # the real ancestor check within each test's filesystem fixture so that
    # unrelated host repositories do not make private-file tests impossible.
    def fixture_checkout(path):
        return any((parent / ".git").exists() for parent in (path, *path.parents)
                   if parent == tmp_path or parent.is_relative_to(tmp_path))
    monkeypatch.setattr(configure, "_inside_checkout", fixture_checkout)


def config_files(directory):
    return {name: (directory / filename).read_bytes() for name, filename in configure.FILES.items()}


def parsed(directory):
    return json.loads((directory / "access.json").read_text()), json.loads((directory / "credentials.json").read_text())


def test_initialization_produces_working_private_files_and_no_console_secrets(tmp_path, capsys):
    directory = tmp_path / "private-config"
    assert configure.main(["init", "--directory", str(directory), "--owner", "operator"]) == 0
    output = capsys.readouterr()
    config, credentials = parsed(directory)
    environment = dotenv_values(directory / "pilot.env")
    token = credentials["tokens"]["operator"]
    assert token.startswith("doxa_") and len(token) >= 40
    assert config["users"][0]["token_sha256"] == token_hash(token)
    assert config["users"][0]["all_projects"]
    assert config["users"][0]["permissions"] == []  # No implicit identity-map reveal.
    assert token not in (directory / "access.json").read_text()
    assert environment["DOXA_ACCESS_MODE"] == "managed"
    assert environment["DOXA_PILOT_WORKSPACE_KIND"] == "organization"
    assert environment["DOXA_ACCESS_CONFIG_FILE"] == str(directory / "access.json")
    for secret in (token, config["session_secret"], environment["DOXA_PRIVACY_VAULT_KEY"]):
        assert secret not in output.out and secret not in output.err
    assert json.loads(output.out)["secrets_printed"] is False
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in directory.iterdir())
    access = AccessControl("managed", environment["DOXA_ACCESS_CONFIG_FILE"])
    assert access.authenticate_token(token, access.configuration())["principal"].id == "operator"


def test_init_is_never_implicit_credential_or_vault_key_rotation(tmp_path):
    directory = tmp_path / "private"
    configure.initialize(directory, "owner")
    original = config_files(directory)
    with pytest.raises(ValueError, match="never overwrites"):
        configure.initialize(directory, "replacement")
    assert config_files(directory) == original


def test_personal_workspace_kind_and_paths_with_spaces(tmp_path):
    directory = tmp_path / "private space"
    configure.initialize(directory, "person", kind="personal")
    environment = dotenv_values(directory / "pilot.env")
    assert environment["DOXA_PILOT_WORKSPACE_KIND"] == "personal"
    assert environment["DOXA_ACCESS_CONFIG_FILE"] == str(directory / "access.json")
    configure.add_user(directory, "reviewer", role="reviewer", project_ids=["selected-project"])
    assert dotenv_values(directory / "pilot.env") == environment


def test_add_user_has_explicit_projects_and_preserves_owner_tokens_key_and_session(tmp_path):
    directory = tmp_path / "private"
    configure.initialize(directory, "owner")
    original = config_files(directory)
    before_config, before_credentials = parsed(directory)
    receipt = configure.add_user(directory, "reviewer", role="reviewer", project_ids=["p2", "p1", "p1"])
    config, credentials = parsed(directory)
    assert receipt["operation"] == "user_added"
    assert config["session_secret"] == before_config["session_secret"]
    assert config["users"][0] == before_config["users"][0]
    assert credentials["tokens"]["owner"] == before_credentials["tokens"]["owner"]
    reviewer = next(user for user in config["users"] if user["id"] == "reviewer")
    assert reviewer["project_ids"] == ["p1", "p2"]
    assert reviewer["all_projects"] is False and reviewer["permissions"] == []
    assert (directory / "pilot.env").read_bytes() == original["environment"]
    assert not (directory / configure.JOURNAL).exists()


def test_existing_user_requires_replace_flag_and_last_owner_cannot_be_removed(tmp_path):
    directory = tmp_path / "private"
    configure.initialize(directory, "owner")
    configure.add_user(directory, "analyst", project_ids=["p1"])
    original = config_files(directory)
    with pytest.raises(ValueError, match="replace-user"):
        configure.add_user(directory, "analyst", role="viewer", project_ids=["p2"])
    assert config_files(directory) == original
    configure.add_user(directory, "analyst", role="viewer", project_ids=["p2"], replace_user=True)
    config, credentials = parsed(directory)
    assert next(user for user in config["users"] if user["id"] == "analyst")["role"] == "viewer"
    assert credentials["tokens"]["analyst"] != json.loads(original["credentials"])["tokens"]["analyst"]
    prior = config_files(directory)
    with pytest.raises(ValueError, match="owner"):
        configure.add_user(directory, "owner", role="viewer", project_ids=["p1"], replace_user=True)
    assert config_files(directory) == prior


def test_token_rotation_requires_explicit_confirmation_and_preserves_every_other_secret(tmp_path):
    directory = tmp_path / "private"
    configure.initialize(directory, "owner")
    configure.add_user(directory, "analyst", project_ids=["p1"])
    original = config_files(directory)
    before_config, before_credentials = parsed(directory)
    with pytest.raises(ValueError, match="confirm"):
        configure.rotate_token(directory, "analyst")
    assert config_files(directory) == original
    configure.rotate_token(directory, "analyst", confirm=True)
    after_config, after_credentials = parsed(directory)
    assert after_config["session_secret"] == before_config["session_secret"]
    assert after_credentials["tokens"]["owner"] == before_credentials["tokens"]["owner"]
    assert after_credentials["tokens"]["analyst"] != before_credentials["tokens"]["analyst"]
    assert (directory / "pilot.env").read_bytes() == original["environment"]
    access = AccessControl("managed", str(directory / "access.json"))
    assert access.authenticate_token(before_credentials["tokens"]["analyst"], access.configuration()) is None
    assert access.authenticate_token(after_credentials["tokens"]["analyst"], access.configuration())


def test_identity_reveal_and_global_access_are_explicit_operator_grants(tmp_path):
    directory = tmp_path / "private"
    configure.initialize(directory, "owner")
    configure.add_user(directory, "admin", role="admin", all_projects=True, identity_reveal=True)
    config, _ = parsed(directory)
    assert config["users"][1]["permissions"] == ["identity_reveal"]
    assert config["users"][1]["all_projects"]
    original = config_files(directory)
    with pytest.raises(ValueError, match="Only an owner or admin"):
        configure.add_user(directory, "analyst", all_projects=True)
    assert config_files(directory) == original


def test_interrupted_update_is_explicitly_recoverable_without_changing_vault_key(tmp_path, monkeypatch):
    directory = tmp_path / "private"
    configure.initialize(directory, "owner")
    original = config_files(directory)
    actual = configure._atomic
    calls = []

    def interrupt(path, value):
        calls.append(path.name)
        if path.name == "access.json":
            raise OSError("simulated disk failure")
        return actual(path, value)

    monkeypatch.setattr(configure, "_atomic", interrupt)
    with pytest.raises(OSError, match="simulated"):
        configure.add_user(directory, "analyst", project_ids=["p1"])
    assert calls == ["credentials.json", "access.json"]
    assert (directory / configure.JOURNAL).exists()
    assert (directory / "access.json").read_bytes() == original["config"]
    assert (directory / "pilot.env").read_bytes() == original["environment"]
    with pytest.raises(ValueError, match="interrupted"):
        configure.rotate_token(directory, "owner", confirm=True)
    with pytest.raises(ValueError, match="confirm"):
        configure.recover(directory)
    monkeypatch.setattr(configure, "_atomic", actual)
    configure.recover(directory, confirm=True)
    config, credentials = parsed(directory)
    assert len(config["users"]) == 2
    assert token_hash(credentials["tokens"]["analyst"]) == config["users"][1]["token_sha256"]
    assert not (directory / configure.JOURNAL).exists()
    assert (directory / "pilot.env").read_bytes() == original["environment"]


def test_recovery_does_not_overwrite_unrelated_operator_changes(tmp_path, monkeypatch):
    directory = tmp_path / "private"
    configure.initialize(directory, "owner")
    actual = configure._atomic
    monkeypatch.setattr(configure, "_atomic", lambda *args: (_ for _ in ()).throw(OSError("interrupted")))
    with pytest.raises(OSError):
        configure.add_user(directory, "analyst", project_ids=["p1"])
    monkeypatch.setattr(configure, "_atomic", actual)
    config, _ = parsed(directory)
    config["session_secret"] = "other-operator-secret-" * 3
    (directory / "access.json").write_text(json.dumps(config))
    modified = config_files(directory)
    with pytest.raises(ValueError, match="changed outside"):
        configure.recover(directory, confirm=True)
    assert config_files(directory) == modified


def test_refuses_repository_directory_symlink_and_insecure_permissions(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / ".git").mkdir()
    with pytest.raises(ValueError, match="outside Git"):
        configure.initialize(checkout / "secrets", "owner")
    with pytest.raises(ValueError, match="outside Git"):
        configure.initialize(tmp_path / "missing" / ".." / "checkout" / "secrets", "owner")
    assert not (checkout / "secrets").exists()
    directory = tmp_path / "private"
    configure.initialize(directory, "owner")
    linked = tmp_path / "linked"
    linked.symlink_to(directory)
    with pytest.raises(ValueError, match="symlink"):
        configure.rotate_token(linked, "owner", confirm=True)
    original = config_files(directory)
    os.chmod(directory / "credentials.json", 0o644)
    with pytest.raises(ValueError, match="private files"):
        configure.rotate_token(directory, "owner", confirm=True)
    assert config_files(directory) == original
    os.chmod(directory / "credentials.json", 0o600)
    os.chmod(directory, 0o755)
    with pytest.raises(ValueError, match="0700"):
        configure.rotate_token(directory, "owner", confirm=True)


def test_cli_errors_never_print_existing_tokens(tmp_path, capsys):
    directory = tmp_path / "private"
    configure.initialize(directory, "owner")
    _, credentials = parsed(directory)
    with pytest.raises(SystemExit) as result:
        configure.main(["rotate-token", "--directory", str(directory), "--user", "owner"])
    assert result.value.code == 2
    captured = capsys.readouterr()
    assert credentials["tokens"]["owner"] not in captured.err + captured.out
    assert "--confirm" in captured.err


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX named-pipe behavior")
def test_helper_rejects_named_pipe_without_blocking(tmp_path):
    path = tmp_path / "not-a-regular-file"
    os.mkfifo(path, mode=0o600)
    with pytest.raises(ValueError, match="regular private files"):
        configure._read_private(path)


@pytest.mark.parametrize("kwargs", [{"user_id": "invalid user"}, {"user_id": "ok", "role": "root"},
                                   {"user_id": "ok", "project_ids": "p1"},
                                   {"user_id": "ok", "all_projects": "false"}])
def test_invalid_user_configuration_does_not_change_files(tmp_path, kwargs):
    directory = tmp_path / "private"
    configure.initialize(directory, "owner")
    original = config_files(directory)
    with pytest.raises(ValueError):
        configure.add_user(directory, **kwargs)
    assert config_files(directory) == original

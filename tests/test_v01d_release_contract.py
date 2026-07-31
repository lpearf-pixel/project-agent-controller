from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/v0.1d-release-gate.yml"
DOCKER_PREFLIGHT = ROOT / "scripts/preflight-docker-v0.1d.py"
RUNBOOK = ROOT / "docs/onboarding/community-selection-host-service.md"


def test_release_workflow_is_read_only_pinned_and_cross_platform() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    lowered = text.lower()

    assert "permissions:\n  contents: read" in text
    assert "pull_request_target" not in text
    assert "runs-on: ubuntu-24.04" in text
    assert "runs-on: macos-15" in text
    assert text.count("timeout-minutes: 20") == 2
    assert "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1" in text
    assert "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97" in text
    assert "astral-sh/setup-uv@c771a70e6277c0a99b617c7a806ffedaca235ff9" in text
    assert "./scripts/verify-v0.1c.sh" in text
    assert text.count("PAC_GITHUB_TOKEN: ${{ github.token }}") == 2
    assert text.count("scripts/preflight-task-v0.2.py") == 2
    assert "scripts/preflight-docker-v0.1d.py" in text
    assert "pac service render" in text
    for forbidden in ("git push", "gh pr", "gh issue", "curl -x post", "permissions: write-all"):
        assert forbidden not in lowered


def test_docker_preflight_uses_existing_get_only_provider_without_control_calls() -> None:
    text = DOCKER_PREFLIGHT.read_text(encoding="utf-8")

    assert "UnixSocketDockerTransport" in text
    assert "DockerEngineProvider" in text
    assert 'validate_docker_request("POST"' in text
    assert "find_container" in text
    assert ".inspect(" in text
    assert ".logs(" in text
    for forbidden in ("subprocess", "docker run", "docker stop", "docker rm", "os.system"):
        assert forbidden not in text


def test_community_host_service_runbook_covers_full_operator_lifecycle() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")

    for required in (
        "chmod 600",
        "pac service render",
        "launchctl bootstrap",
        "launchctl bootout",
        "systemctl --user daemon-reload",
        "systemctl --user enable --now",
        "journalctl --user-unit",
        "/health",
        "controller drain",
        "controller emergency-stop",
        "clear-emergency-stop",
        "RECOVERING",
        "controller.db",
        "不会删除",
    ):
        assert required in text
    assert "docker compose down" not in text
    assert "git push" not in text

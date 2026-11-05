from __future__ import annotations

from pathlib import Path

import pytest

from shared import preflight_checks as preflight_module
from shared.preflight_checks import PipelinePreflight, PreflightError


class FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self.payload


def build_preflight(tmp_path: Path, *, node_id=None) -> PipelinePreflight:
    return PipelinePreflight(
        config={
            "webodm": {
                "url": "https://webodm.invalid",
                "username": "test-user",
                "password": "test-password",
                "node_id": node_id,
            }
        },
        source_dir=tmp_path / "source",
        surveys_root=tmp_path / "surveys",
        year=2026,
    )


def install_node_response(monkeypatch, preflight, nodes) -> None:
    monkeypatch.setattr(
        preflight,
        "_check_webodm_server",
        lambda _stage: "fake-webodm-jwt",
    )
    monkeypatch.setattr(
        preflight_module.requests,
        "get",
        lambda *args, **kwargs: FakeResponse(nodes),
    )


def test_configured_offline_node_error_omits_credentials(monkeypatch, tmp_path):
    preflight = build_preflight(tmp_path, node_id=5)
    install_node_response(
        monkeypatch,
        preflight,
        [
            {
                "id": 5,
                "name": "processing-five",
                "online": False,
                "status": "offline",
                "token": "node-five-secret",
                "password": "node-five-password",
                "nested": {"api_key": "nested-secret"},
            }
        ],
    )

    with pytest.raises(PreflightError) as exc_info:
        preflight._check_webodm_node("webodm")

    message = str(exc_info.value)
    assert "configured WebODM processing node appears offline" in message
    assert "'id': 5" in message
    assert "'name': 'processing-five'" in message
    assert "'online': False" in message
    assert "node-five-secret" not in message
    assert "node-five-password" not in message
    assert "nested-secret" not in message
    assert "'token'" not in message
    assert "'password'" not in message


def test_all_offline_nodes_error_uses_only_safe_diagnostics(monkeypatch, tmp_path):
    preflight = build_preflight(tmp_path)
    install_node_response(
        monkeypatch,
        preflight,
        [
            {
                "id": 3,
                "name": "processing-three",
                "status": "offline",
                "token": "node-three-secret",
            },
            {
                "id": 5,
                "name": "processing-five",
                "reachable": False,
                "password": "node-five-password",
            },
        ],
    )

    with pytest.raises(PreflightError) as exc_info:
        preflight._check_webodm_node("webodm")

    message = str(exc_info.value)
    assert "all WebODM processing nodes appear offline" in message
    assert "'id': 3" in message
    assert "'id': 5" in message
    assert "'status': 'offline'" in message
    assert "'reachable': False" in message
    assert "node-three-secret" not in message
    assert "node-five-password" not in message
    assert "'token'" not in message
    assert "'password'" not in message

import socket

import pytest

from tests.fakes import FakeWebODM, PermanentWebODMError, TransientWebODMError


def test_fake_webodm_returns_deterministic_project_and_task_identifiers(
    sample_dataset_dir,
):
    fake = FakeWebODM()

    assert fake.create_project("project-one") == 100
    assert fake.create_project("project-two") == 101
    assert (
        fake.create_task_with_images(100, "task-one", str(sample_dataset_dir))
        == "task-0001"
    )
    assert (
        fake.create_task_with_images(100, "task-two", str(sample_dataset_dir))
        == "task-0002"
    )


def test_fake_webodm_records_calls_arguments_and_order(sample_dataset_dir, tmp_path):
    fake = FakeWebODM()
    fake.authenticate()
    fake.preflight()
    project_id = fake.create_project("recorded", description="test project")
    task_id = fake.create_task_with_images(
        project_id,
        "recorded-task",
        str(sample_dataset_dir),
        options={"orthophoto-resolution": 5},
        processing_node=2,
    )
    fake.download_asset_safe(project_id, task_id, "orthophoto.tif", tmp_path / "out.tif")

    assert [call.method for call in fake.calls] == [
        "authenticate",
        "preflight",
        "create_project",
        "create_task_with_images",
        "download_asset_safe",
    ]
    task_call = fake.calls_for("create_task_with_images")[0]
    assert task_call.args == (project_id, "recorded-task", str(sample_dataset_dir))
    assert task_call.kwargs["options"] == {"orthophoto-resolution": 5}
    assert task_call.kwargs["processing_node"] == 2


def test_fake_webodm_transient_failures_are_counted():
    fake = FakeWebODM(transient_failures={"create_project": 2})

    with pytest.raises(TransientWebODMError):
        fake.create_project("retry-me")
    with pytest.raises(TransientWebODMError):
        fake.create_project("retry-me")

    assert fake.create_project("retry-me") == 100
    assert len(fake.calls_for("create_project")) == 3


def test_fake_webodm_permanent_failures_repeat():
    fake = FakeWebODM(permanent_failures={"authenticate"})

    with pytest.raises(PermanentWebODMError):
        fake.authenticate()
    with pytest.raises(PermanentWebODMError):
        fake.authenticate()

    assert len(fake.calls_for("authenticate")) == 2
    assert not fake.authenticated


def test_fake_webodm_never_invokes_network(monkeypatch):
    attempts = []

    def reject_network(*args, **kwargs):
        attempts.append((args, kwargs))
        raise AssertionError("network invoked")

    monkeypatch.setattr(socket, "create_connection", reject_network)
    monkeypatch.setattr(socket.socket, "connect", reject_network)

    fake = FakeWebODM()
    fake.authenticate()
    fake.create_project("offline")

    assert attempts == []

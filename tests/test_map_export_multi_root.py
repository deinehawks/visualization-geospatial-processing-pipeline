from __future__ import annotations

import os
from pathlib import Path

import pytest

from map import parse_survey_roots
from modules.map_export.map_package_exporter import (
    _collect_from_survey_roots,
    _normalize_survey_roots,
)


def test_parse_survey_roots_accepts_repeated_cli_values(tmp_path):
    first = tmp_path / 'surveys-z'
    second = tmp_path / 'surveys-x'

    roots = parse_survey_roots(
        [str(first), str(second), str(first)],
        None,
    )

    assert roots == [first, second]


def test_parse_survey_roots_accepts_environment_path_list(tmp_path):
    first = tmp_path / 'surveys-z'
    second = tmp_path / 'surveys-x'

    roots = parse_survey_roots(
        None,
        os.pathsep.join((str(first), str(second))),
    )

    assert roots == [first, second]


def test_normalize_survey_roots_preserves_legacy_single_root(tmp_path):
    root = tmp_path / 'surveys'

    assert _normalize_survey_roots(root, None) == [root]


def test_collect_from_survey_roots_falls_back_per_survey(tmp_path):
    first = tmp_path / 'surveys-z'
    second = tmp_path / 'surveys-x'
    expected_roots = {
        'survey-one': first,
        'survey-two': second,
    }
    calls = []

    def fake_collector(*, surveys_root, db_path, survey_names):
        assert db_path == tmp_path / 'pipeline.db'
        survey_name = survey_names[0]
        calls.append((survey_name, surveys_root))
        if surveys_root != expected_roots[survey_name]:
            raise FileNotFoundError(survey_name)
        return [surveys_root / f'{survey_name}.tif']

    selected = _collect_from_survey_roots(
        survey_roots=[first, second],
        db_path=tmp_path / 'pipeline.db',
        survey_names=['survey-one', 'survey-two'],
        collector=fake_collector,
        artifact_label='test artifact',
    )

    assert selected == [
        first / 'survey-one.tif',
        second / 'survey-two.tif',
    ]
    assert calls == [
        ('survey-one', first),
        ('survey-two', first),
        ('survey-two', second),
    ]


def test_collect_from_survey_roots_reports_all_attempted_roots(tmp_path):
    first = tmp_path / 'surveys-z'
    second = tmp_path / 'surveys-x'

    def missing_collector(**_kwargs):
        raise FileNotFoundError('missing')

    with pytest.raises(FileNotFoundError) as exc_info:
        _collect_from_survey_roots(
            survey_roots=[first, second],
            db_path=None,
            survey_names=['survey-one'],
            collector=missing_collector,
            artifact_label='boundary file',
        )

    message = str(exc_info.value)
    assert 'survey-one' in message
    assert str(first) in message
    assert str(second) in message
    assert isinstance(exc_info.value.__cause__, FileNotFoundError)


def test_collect_from_survey_roots_does_not_hide_non_missing_errors(tmp_path):
    roots = [tmp_path / 'surveys-z', tmp_path / 'surveys-x']
    calls = []

    def broken_collector(*, surveys_root, **_kwargs):
        calls.append(surveys_root)
        raise PermissionError('access denied')

    with pytest.raises(PermissionError, match='access denied'):
        _collect_from_survey_roots(
            survey_roots=roots,
            db_path=None,
            survey_names=['survey-one'],
            collector=broken_collector,
            artifact_label='boundary file',
        )

    assert calls == [roots[0]]

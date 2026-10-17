"""One place for every AI operation's mode: the central map, the disable list, the legacy
per-operation setting as a fallback, and template for anything unknown."""

import dataclasses

import pytest

from app.ai.contracts import Mode
from app.ai.operations import case_brief
from app.ai.orchestrator import mode_for
from app.core.config import Settings


def _settings(**kw) -> Settings:
    return Settings(_env_file=None, **kw)


SPEC = case_brief.SPEC
NEW = dataclasses.replace(SPEC, id="future_op", mode_setting=None)


def test_the_central_map_wins_over_the_legacy_setting():
    s = _settings(ai_case_brief_mode="prototype", ai_operation_modes={"case_brief": "shadow"})
    assert mode_for(SPEC, s) is Mode.SHADOW


def test_the_legacy_setting_still_works_without_a_map_entry():
    assert mode_for(SPEC, _settings(ai_case_brief_mode="shadow")) is Mode.SHADOW


def test_a_new_operation_needs_no_settings_field():
    assert mode_for(NEW, _settings()) is Mode.TEMPLATE
    assert mode_for(NEW, _settings(ai_operation_modes={"future_op": "prototype"})) is Mode.PROTOTYPE


def test_the_disable_list_forces_template():
    s = _settings(
        ai_operation_modes={"case_brief": "prototype"}, ai_operations_disabled=["case_brief"]
    )
    assert mode_for(SPEC, s) is Mode.TEMPLATE


@pytest.mark.parametrize("value", ["", "on", "PROTOTYPE!", "cloud"])
def test_unknown_values_mean_template(value):
    assert mode_for(NEW, _settings(ai_operation_modes={"future_op": value})) is Mode.TEMPLATE


def test_the_map_is_read_from_the_environment_as_json(monkeypatch):
    monkeypatch.setenv("NBA_AI_OPERATION_MODES", '{"case_brief": "shadow"}')
    monkeypatch.setenv("NBA_AI_OPERATIONS_DISABLED", '["future_op"]')
    s = _settings()
    assert s.ai_operation_modes == {"case_brief": "shadow"}
    assert s.ai_operations_disabled == ["future_op"]

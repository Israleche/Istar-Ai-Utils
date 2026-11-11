"""Godot harness integration; set GODOT_BIN to a Godot 4 console executable."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parents[1] / "godot" / "tick_harness.gd"
GODOT = os.environ.get("GODOT_BIN") or shutil.which("godot")
PREFIX = "ISTAR_TICK_RESULT="


@pytest.fixture
def project(tmp_path):
    (tmp_path / "project.godot").write_text(
        'config_version=5\n[application]\nconfig/name="Istar isolated tick test"\n',
        encoding="utf-8",
    )
    (tmp_path / "fixture.gd").write_text(
        """extends RefCounted
var position := 0
var seen := []
func setup(seed: int) -> void:
    position = seed
func step(tick: int, actions: Dictionary) -> void:
    if actions.get("right", false):
        position += 1
    seen.append(tick)
func snapshot() -> Dictionary:
    return {"position": position, "seen": seen}
""",
        encoding="utf-8",
    )
    return tmp_path


def run_harness(project, plan, fixture="res://fixture.gd"):
    if not GODOT:
        pytest.skip("set GODOT_BIN or add Godot to PATH for integration tests")
    env = os.environ.copy()
    env["APPDATA"] = str(project / "appdata")
    env["XDG_DATA_HOME"] = str(project / "data")
    result = subprocess.run(
        [
            GODOT,
            "--headless",
            "--path",
            str(project),
            "--script",
            str(HARNESS),
            "--",
            fixture,
            json.dumps(plan, separators=(",", ":")),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    lines = [
        line[len(PREFIX) :]
        for line in result.stdout.splitlines()
        if line.startswith(PREFIX)
    ]
    assert len(lines) == 1, (result.returncode, result.stdout, result.stderr)
    return result.returncode, json.loads(lines[0])


def test_fixed_ticks_are_repeatable_and_input_persists(project):
    plan = {
        "ticks": 5,
        "seed": 7,
        "events": [
            {"tick": 1, "action": "right", "pressed": True},
            {"tick": 4, "action": "right", "pressed": False},
        ],
    }
    expected = (
        0,
        {"ok": True, "ticks": 5, "state": {"position": 10, "seen": [0, 1, 2, 3, 4]}},
    )
    assert run_harness(project, plan) == expected
    assert run_harness(project, plan) == expected


@pytest.mark.parametrize(
    "plan",
    [
        {"ticks": 10001, "seed": 0, "events": []},
        {
            "ticks": 1,
            "seed": 0,
            "events": [{"tick": 1, "action": "x", "pressed": True}],
        },
        {"ticks": 1, "seed": 0, "events": [{"tick": 0, "action": "x", "pressed": 1}]},
        {"ticks": True, "seed": 0, "events": []},
    ],
)
def test_rejects_invalid_plan(project, plan):
    assert run_harness(project, plan) == (2, {"ok": False, "error": "invalid plan"})


def test_zero_ticks(project):
    assert run_harness(project, {"ticks": 0, "seed": 3, "events": []}) == (
        0,
        {"ok": True, "ticks": 0, "state": {"position": 3, "seen": []}},
    )

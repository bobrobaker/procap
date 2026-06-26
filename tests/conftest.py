"""Shared fixtures. The synthetic clip is generated on demand (it is gitignored) and
extracted once per test session, so stage tests run against real keyframes/segments."""
from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "corpus" / "synthetic"
VIDEO = CORPUS / "labeled_demo.mp4"
LABELS = CORPUS / "labeled_demo.labels.json"


@pytest.fixture(scope="session")
def synthetic_video() -> Path:
    if not VIDEO.exists():
        runpy.run_path(str(ROOT / "corpus" / "make_synthetic.py"), run_name="__main__")
    assert VIDEO.exists(), "synthetic corpus generation failed"
    return VIDEO


@pytest.fixture(scope="session")
def synthetic_labels(synthetic_video) -> list[dict]:
    return json.loads(LABELS.read_text())


@pytest.fixture(scope="session")
def extracted_run(synthetic_video, tmp_path_factory):
    from procap.extract import extract_keyframes
    from procap.run import Run
    run = Run(tmp_path_factory.mktemp("run"))
    extract_keyframes(synthetic_video, run=run)
    return run

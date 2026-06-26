"""VLM enhancement layer: each keyed branch (golden refine, procedure titling, semantic
audit) exercised with a stubbed VLM, plus the guarantee that the offline path (available
False) is byte-for-byte the heuristic baseline.

No network/credits: a StubVLM stands in for procap.vlm.VLM, returning canned replies and
recording calls. Real keyframe images are never read (the stub ignores image_paths), so
these tests build lightweight Keyframe/Segment fixtures directly.
"""
from __future__ import annotations

import pytest

from procap import golden as golden_stage
from procap import procedure as procedure_stage
from procap.audit import audit
from procap.model import (
    Keyframe, Procedure, ProcedureStep, Segment, SegmentKind,
)
from procap.vlm import VLM, extract_json


class StubVLM:
    """available-True stand-in for VLM. `replies` is a str (same every call), a list
    (consumed in call order), or a callable(prompt, image_paths) -> str."""

    def __init__(self, replies):
        self._replies = replies
        self.calls: list[dict] = []
        self.available = True

    def ask(self, prompt, image_paths=None, max_tokens=1024):
        self.calls.append({"prompt": prompt, "image_paths": list(image_paths or [])})
        r = self._replies
        if callable(r):
            return r(prompt, image_paths)
        if isinstance(r, list):
            return r[len(self.calls) - 1]
        return r


def _kf(index: int) -> Keyframe:
    return Keyframe(index=index, t=float(index), t_end=index + 1.0,
                    path=f"kf{index}.png", change_score=0.5, phash="0" * 16)


def _seg(kind: str, idxs: list[int], confidence: float = 0.7) -> Segment:
    return Segment(start_t=float(idxs[0]), end_t=idxs[-1] + 1.0, keyframe_indexes=idxs,
                   kind=kind, reason="heuristic prior", confidence=confidence,
                   judged_by="heuristic")


# --------------------------------------------------------------------------- extract_json

def test_extract_json_tolerates_surrounding_prose():
    reply = 'Sure! Here is my verdict:\n```json\n{"kind": "dross", "confidence": 0.9}\n```\nDone.'
    assert extract_json(reply) == {"kind": "dross", "confidence": 0.9}


def test_extract_json_raises_on_no_object():
    with pytest.raises(ValueError):
        extract_json("I cannot determine that.")


# --------------------------------------------------------------------------- golden refine

def test_refine_flips_on_confident_verdict():
    segs = [_seg(SegmentKind.GOLDEN.value, [1])]
    kfs = [_kf(1)]
    stub = StubVLM('{"kind": "dross", "confidence": 0.95, "reason": "reverted misclick"}')
    out = golden_stage.refine_with_vlm(segs, kfs, vlm=stub)
    assert out[0].kind == SegmentKind.DROSS.value
    assert out[0].confidence == 0.95
    assert out[0].judged_by == "vlm"
    assert out[0].reason == "reverted misclick"
    # the segment's keyframe image was actually handed to the model
    assert stub.calls[0]["image_paths"] == ["kf1.png"]


def test_refine_keeps_prior_when_vlm_hedges():
    segs = [_seg(SegmentKind.GOLDEN.value, [1])]
    stub = StubVLM('{"kind": "dross", "confidence": 0.3}')
    out = golden_stage.refine_with_vlm(segs, [_kf(1)], vlm=stub)
    assert out[0].kind == SegmentKind.GOLDEN.value  # not flipped
    assert out[0].judged_by == "heuristic"
    assert out[0].confidence == 0.7


def test_refine_skips_already_confident_segments():
    segs = [_seg(SegmentKind.GOLDEN.value, [1], confidence=0.9)]
    stub = StubVLM('{"kind": "dross", "confidence": 0.99}')
    out = golden_stage.refine_with_vlm(segs, [_kf(1)], vlm=stub)
    assert stub.calls == []                     # no VLM call spent
    assert out[0].judged_by == "heuristic"


def test_refine_survives_garbage_reply():
    segs = [_seg(SegmentKind.GOLDEN.value, [1])]
    stub = StubVLM("the model rambled and returned no JSON")
    out = golden_stage.refine_with_vlm(segs, [_kf(1)], vlm=stub)
    assert out[0].kind == SegmentKind.GOLDEN.value
    assert out[0].judged_by == "heuristic"


def test_refine_ignores_unknown_kind():
    segs = [_seg(SegmentKind.GOLDEN.value, [1])]
    stub = StubVLM('{"kind": "maybe", "confidence": 0.99}')
    out = golden_stage.refine_with_vlm(segs, [_kf(1)], vlm=stub)
    assert out[0].kind == SegmentKind.GOLDEN.value
    assert out[0].judged_by == "heuristic"


def test_refine_offline_is_passthrough():
    segs = [_seg(SegmentKind.GOLDEN.value, [1])]
    out = golden_stage.refine_with_vlm(segs, [_kf(1)], vlm=VLM(api_key=None))
    assert out[0].kind == SegmentKind.GOLDEN.value
    assert out[0].judged_by == "heuristic"
    assert out[0].confidence == 0.7


# ------------------------------------------------------------------------ procedure titling

def _golden_segs() -> list[Segment]:
    return [_seg(SegmentKind.GOLDEN.value, [0]), _seg(SegmentKind.GOLDEN.value, [1])]


def test_describe_uses_vlm_title_and_description():
    stub = StubVLM('{"title": "Start the feed pump", "description": "Clicked the pump toggle on."}')
    proc = procedure_stage.synthesize(
        _golden_segs(), source_video="demo.mp4", vlm=stub, keyframes=[_kf(0), _kf(1)])
    assert proc.steps[0].title == "Start the feed pump"
    assert proc.steps[0].description == "Clicked the pump toggle on."
    assert "fill in" not in proc.steps[0].title
    # keyframe image for the first segment was shown to the model
    assert stub.calls[0]["image_paths"] == ["kf0.png"]


def test_describe_falls_back_to_placeholder_on_error():
    stub = StubVLM("no json here")
    proc = procedure_stage.synthesize(
        _golden_segs(), source_video="demo.mp4", vlm=stub, keyframes=[_kf(0), _kf(1)])
    assert all("fill in" in s.title for s in proc.steps)


def test_describe_placeholder_when_keyframes_absent():
    stub = StubVLM('{"title": "Should not be used", "description": "x"}')
    proc = procedure_stage.synthesize(_golden_segs(), source_video="demo.mp4", vlm=stub)
    assert all("fill in" in s.title for s in proc.steps)
    assert stub.calls == []  # nothing to show -> no call


def test_describe_offline_is_placeholder():
    proc = procedure_stage.synthesize(
        _golden_segs(), source_video="demo.mp4", vlm=VLM(api_key=None), keyframes=[_kf(0), _kf(1)])
    assert all("fill in" in s.title for s in proc.steps)


# ----------------------------------------------------------------------------- semantic audit

def _proc(titles: list[str]) -> Procedure:
    steps = [ProcedureStep(index=i, title=t, description=f"did {t}", keyframe_indexes=[i],
                           start_t=float(i), end_t=i + 1.0, est_seconds=1.0)
             for i, t in enumerate(titles)]
    return Procedure(title="P", source_video="demo.mp4", steps=steps,
                     total_est_seconds=float(len(steps)))


def _write_doc(tmp_path, steps: list[str]):
    p = tmp_path / "written.md"
    p.write_text("# Doc\n\n" + "\n".join(f"{i + 1}. {s}" for i, s in enumerate(steps)) + "\n")
    return p


def test_audit_semantic_flags_missing_step(tmp_path):
    proc = _proc(["Pump", "Valve", "Flow"])
    doc = _write_doc(tmp_path, ["Turn on pump", "Open valve"])
    # Pump->1, Valve->2, Flow->none
    stub = StubVLM(['{"match": 1}', '{"match": 2}', '{"match": 0}'])
    report = audit(proc, doc, vlm=stub)
    missing = [f for f in report.findings if f.kind == "missing_step"]
    assert len(missing) == 1 and missing[0].procedure_step_index == 2
    assert report.coverage == pytest.approx(2 / 3, abs=1e-3)


def test_audit_semantic_flags_out_of_order(tmp_path):
    proc = _proc(["Pump", "Valve"])
    doc = _write_doc(tmp_path, ["Open valve", "Turn on pump"])
    # Pump matches doc step 2; Valve matches doc step 1 -> goes backwards => out_of_order
    stub = StubVLM(['{"match": 2}', '{"match": 1}'])
    report = audit(proc, doc, vlm=stub)
    ooo = [f for f in report.findings if f.kind == "out_of_order"]
    assert len(ooo) == 1 and ooo[0].procedure_step_index == 1


def test_audit_semantic_flags_under_documented(tmp_path):
    proc = _proc(["Pump"])
    doc = _write_doc(tmp_path, ["pump"])
    stub = StubVLM('{"match": 1, "under_documented": true}')
    report = audit(proc, doc, vlm=stub)
    assert any(f.kind == "under_documented" for f in report.findings)
    assert report.coverage == 1.0


def test_audit_semantic_survives_bad_reply(tmp_path):
    proc = _proc(["Pump"])
    doc = _write_doc(tmp_path, ["Turn on pump"])
    stub = StubVLM("not json")  # -> treated as unmatched
    report = audit(proc, doc, vlm=stub)
    assert any(f.kind == "missing_step" for f in report.findings)


def test_audit_offline_matches_positional_baseline(tmp_path):
    proc = _proc(["Pump", "Valve", "Flow"])
    doc = _write_doc(tmp_path, ["Turn on pump", "Open valve"])
    report = audit(proc, doc, vlm=VLM(api_key=None))
    # Positional baseline: 2 covered, step 3 missing, no out_of_order/under_documented.
    assert report.coverage == pytest.approx(2 / 3, abs=1e-3)
    kinds = [f.kind for f in report.findings]
    assert "missing_step" in kinds
    assert "out_of_order" not in kinds

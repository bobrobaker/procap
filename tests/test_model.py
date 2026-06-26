from procap.model import (
    Keyframe, Segment, SegmentKind, ProcedureStep, Procedure,
    AuditFinding, AuditReport, FindingKind,
)


def test_keyframe_round_trip():
    k = Keyframe(index=2, t=1.0, t_end=3.0, path="kf.png", change_score=0.4, phash="abc")
    assert Keyframe.from_dict(k.to_dict()) == k


def test_segment_duration_and_round_trip():
    s = Segment(start_t=1.0, end_t=4.5, keyframe_indexes=[1, 2], kind=SegmentKind.GOLDEN.value,
                reason="x")
    assert s.duration == 3.5
    assert Segment.from_dict(s.to_dict()) == s


def test_procedure_round_trip():
    step = ProcedureStep(index=0, title="t", description="d", keyframe_indexes=[0],
                         start_t=0.0, end_t=2.0, est_seconds=2.0)
    p = Procedure(title="T", source_video="v.mp4", steps=[step], total_est_seconds=2.0)
    assert Procedure.from_dict(p.to_dict()) == p


def test_audit_round_trip():
    r = AuditReport(written_doc="d.md", coverage=0.5,
                    findings=[AuditFinding(kind=FindingKind.MISSING_STEP.value, detail="x",
                                           procedure_step_index=1)])
    assert AuditReport.from_dict(r.to_dict()) == r

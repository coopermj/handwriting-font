from hwfont_schema import CandidateStatus


def test_candidate_status_members():
    assert CandidateStatus.pending.value == "pending"
    assert CandidateStatus.needs_review.value == "needs_review"
    assert {s.value for s in CandidateStatus} == {"pending", "needs_review"}

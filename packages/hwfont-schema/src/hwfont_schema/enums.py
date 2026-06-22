from enum import Enum


class Kind(str, Enum):
    single = "single"
    ligature = "ligature"


class PositionInWord(str, Enum):
    initial = "initial"
    medial = "medial"
    final = "final"
    isolated = "isolated"


class ReviewStatus(str, Enum):
    pending = "pending"
    accepted = "accepted"
    rejected = "rejected"
    needs_review = "needs_review"


class Quality(str, Enum):
    good = "good"
    marginal = "marginal"
    bad = "bad"


class CandidateStatus(str, Enum):
    pending = "pending"
    needs_review = "needs_review"

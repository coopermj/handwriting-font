from __future__ import annotations

from review.errors import ReviewError
from review.inputs import PageInputs, ReviewInputs, load_inputs

__version__ = "0.1.0"

__all__ = ["PageInputs", "ReviewError", "ReviewInputs", "__version__", "load_inputs"]

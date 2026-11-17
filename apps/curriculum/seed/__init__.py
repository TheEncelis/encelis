"""Seed curriculum: (level metadata, units) pairs in CEFR order."""
from . import a1, a2, b1, b2, upper

CONTENT = [
    (a1.LEVEL, a1.UNITS),
    (a2.LEVEL, a2.UNITS),
    (b1.LEVEL, b1.UNITS),
    (b2.LEVEL, b2.UNITS),
    (upper.C1, upper.C1_UNITS),
    (upper.C2, upper.C2_UNITS),
]

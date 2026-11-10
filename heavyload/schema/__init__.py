"""Result schema, unit conventions and v1 -> v2 migration.

All changes are ADDITIVE: v1 keys are never removed or coerced, so existing
report/grading/UI consumers keep working while new consumers can rely on the
normalised v2 fields.
"""

from .metrics import (  # noqa: F401
    SCHEMA_VERSION,
    BENCHMARK_MODES,
    VISIBILITY_LEVELS,
    VERDICT_CODES,
    empty_metrics,
    empty_target,
    empty_fingerprint,
    visibility_map,
    can_compare,
    verdict_code,
)
from .migrate import ensure_current, is_current  # noqa: F401

__all__ = [
    "SCHEMA_VERSION", "BENCHMARK_MODES", "VISIBILITY_LEVELS", "VERDICT_CODES",
    "empty_metrics", "empty_target", "empty_fingerprint", "visibility_map",
    "can_compare", "verdict_code", "ensure_current", "is_current",
]

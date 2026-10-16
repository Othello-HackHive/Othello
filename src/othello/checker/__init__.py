"""Part 2: the independent checker and scorer.

Main entry points:
    check_design(plan, design, cfg)  -> ValidationResult
    score_design(plan, design, cfg)  -> Metrics
    coerce_design(value)             -> Design   (accepts a Design or a same-shaped dict)
"""

from othello.checker.validate import check_design, coerce_design, is_valid, score_design

__all__ = ["check_design", "coerce_design", "is_valid", "score_design"]

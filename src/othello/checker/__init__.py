"""Part 2: the independent checker and scorer.

Main entry points:
    check_design(plan, design, cfg)  -> ValidationResult
    score_design(plan, design, cfg)  -> Metrics
"""

from othello.checker.validate import check_design, is_valid, score_design

__all__ = ["check_design", "is_valid", "score_design"]

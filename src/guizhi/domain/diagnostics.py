
"""Failure facts reported by the check that observed them."""
from typing import Literal

Category = Literal["data", "parameter", "tool", "workflow", "environment", "model", "association", "undetermined"]


class Fault(ValueError):
    def __init__(self, category: Category, code: str, message: str, **evidence):
        super().__init__(message)
        self.category = category
        self.code = code
        self.evidence = evidence

    def fact(self):
        return {"category": self.category, "code": self.code, "evidence": self.evidence}

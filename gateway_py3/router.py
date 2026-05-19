from __future__ import annotations

import re
from typing import Any, Dict, List

from .catalog_loader import OperationCatalog


TOKEN_RE = re.compile(r"[A-Za-z0-9_\.]+")


def _tokens(text: str) -> List[str]:
    return [t.lower() for t in TOKEN_RE.findall(text or "")]


def _contains(text: str, needle: str) -> bool:
    return bool(needle) and needle.lower() in text.lower()


class OperationRouter:
    def __init__(self, catalog: OperationCatalog, default_limit: int = 8, hard_limit: int = 12):
        self.catalog = catalog
        self.default_limit = default_limit
        self.hard_limit = hard_limit

    def select(self, command: str, context: Dict[str, Any] | None = None, limit: int | None = None) -> List[Dict[str, Any]]:
        limit = min(limit or self.default_limit, self.hard_limit)
        command_tokens = set(_tokens(command))
        scored = []

        for operation in self.catalog.all_operations():
            score = self._score_operation(command, command_tokens, operation, context or {})
            if score > 0:
                scored.append((score, operation["id"], operation))

        scored.sort(key=lambda item: (-item[0], item[1]))
        return [operation for _, _, operation in scored[:limit]]

    def fallback(self, command: str, context: Dict[str, Any] | None = None) -> List[Dict[str, Any]]:
        preferred_ids = [
            "context.list_layers",
            "context.describe_layer",
            "context.list_fields",
            "view.zoom_to_layer",
            "selection.select_by_attribute",
            "selection.select_by_location",
            "selection.clear_selection",
            "analysis.buffer"
        ]
        operations = []
        for operation_id in preferred_ids:
            if operation_id in self.catalog.operations:
                operations.append(self.catalog.operations[operation_id])
        return operations[:self.default_limit]

    def _score_operation(self, command: str, command_tokens: set[str], operation: Dict[str, Any], context: Dict[str, Any]) -> int:
        haystack = " ".join([
            operation["id"],
            operation["category"],
            operation["summary"],
            " ".join(operation.get("keywords", []))
        ]).lower()
        score = 0

        for token in command_tokens:
            if token and token in haystack:
                score += 2

        for keyword in operation.get("keywords", []):
            if _contains(command, keyword):
                score += 5

        if operation["side_effects"] == "writes_data" and any(word in command for word in ["导出", "生成", "创建", "缓冲", "裁剪", "融合", "投影", "连接"]):
            score += 2
        if operation["side_effects"] == "changes_map" and any(word in command for word in ["缩放", "显示", "隐藏", "选择", "刷新"]):
            score += 2

        layer_names = [layer.get("name", "") for layer in context.get("layers", [])]
        if any(_contains(command, name) for name in layer_names):
            if operation.get("context_requirements", {}).get("requires_layers"):
                score += 1

        return score

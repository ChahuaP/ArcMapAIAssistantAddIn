"""Operation and skill identities shared by the task runner and MCP."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Operation:
    title: str
    materials: tuple[str, ...]
    version: str = "1.0.0"
    rule: str | None = None


OPERATIONS = {
    "vector_inventory": Operation("矢量清点", ("项目图层",)),
    "control_check": Operation("管控线核查", ("项目范围", "管控范围"),
                               rule="planar-union-intersection@1.0.0"),
}

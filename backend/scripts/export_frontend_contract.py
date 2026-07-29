"""导出前端使用的 OpenAPI，并把 WebSocket 事件 Schema 合并到同一契约中。"""

import argparse
import json
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from agenthub.adapters.protocol import AgentEvent
from agenthub.main import create_app
from agenthub.schemas.domain import EventEnvelope


def _move_definitions(schema: dict[str, Any], components: dict[str, Any]) -> dict[str, Any]:
    """把 Pydantic 的本地定义迁入 OpenAPI components，供同一个生成器解析。"""
    definitions = schema.pop("$defs", {})
    normalized = json.loads(
        json.dumps({"schema": schema, "definitions": definitions}).replace(
            "#/$defs/", "#/components/schemas/"
        )
    )
    components.update(normalized["definitions"])
    return normalized["schema"]


def export_contract(output: Path) -> None:
    """生成包含 REST 与实时事件的单一 OpenAPI 文档。"""
    contract = create_app().openapi()
    components: dict[str, Any] = contract.setdefault("components", {}).setdefault("schemas", {})
    components["EventEnvelope"] = _move_definitions(EventEnvelope.model_json_schema(), components)
    components["AgentEvent"] = _move_definitions(TypeAdapter(AgentEvent).json_schema(), components)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(contract, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    export_contract(args.output.resolve())


if __name__ == "__main__":
    main()

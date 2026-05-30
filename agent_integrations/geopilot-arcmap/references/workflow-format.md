# GeoPilot Workflow Format

## Workflow object

```json
{
  "action": "execute",
  "summary": "一句中文说明。",
  "steps": [
    {
      "id": "step_1",
      "operation": "view.refresh_view",
      "arguments": {},
      "reason": "刷新当前地图视图。"
    }
  ]
}
```

`action` must be one of:

- `execute`: contains one or more executable steps.
- `clarify`: asks one clear question and has no steps.
- `unsupported`: explains missing capability and has no steps.
- `answer`: normal answer with no ArcMap execution and no steps.

## Step rules

- Every execute step requires `id`, `operation`, `arguments`, and `reason`.
- `operation` must be a registered operation id from `/api/capabilities`.
- `arguments` must match the operation schema exactly. Unknown arguments are rejected.
- Later steps may reference earlier outputs as `from_step:step_id` when the produced layer name is not enough.
- Writes-data operations add their output layer to ArcMap automatically; do not add a separate `layer.add_layer` for generated outputs.

## Structured where

Leaf conditions:

```json
{"field":"NAME","op":"eq","value":"鼓楼区"}
{"field":"NAME","op":"like","value":"%南京%"}
{"field":"AREA","op":"gt","value":1000}
{"field":"TYPE","op":"in","values":["A","B"]}
{"field":"AREA","op":"between","values":[10,20]}
{"field":"NAME","op":"is_not_null"}
```

Boolean conditions:

```json
{
  "op": "and",
  "conditions": [
    {"field":"NAME","op":"like","value":"%街道%"},
    {"field":"CLASS","op":"eq","value":"乔木"}
  ]
}
```

Allowed operators: `eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `between`, `in`, `like`, `is_null`, `is_not_null`, `and`, `or`, `not`.

## Output arguments

- Never pass `output_path`.
- Always pass `output_name` for writes-data operations.
- Use `output_workspace` for geodatabase output only when the operation schema includes it.
- Use `output_folder` for folder or shapefile/KMZ/file output only when the operation schema includes it.
- Use `output_format` only when the schema declares it.
- Do not include file extensions in `output_name`.

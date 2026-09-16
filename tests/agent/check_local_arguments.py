"""Real Ollama first-call acceptance with the production MCP schemas.

Only generates tool calls; never executes them or supplies fake tool results.
Run with the installed Python after adding the repository to sys.path.
"""
import asyncio
import json
from pathlib import Path
import urllib.request
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from fastmcp import Client
from server.main import mcp, _catalog
from server.codegen import tool_name
from server.precheck import check
from server.tool_contract import lower_arguments

CASES = [
    ('给 nanjing 图层创建 1 千米缓冲区，输出名 nanjing_buffered。', 'analysis.buffer'),
    ('给 nanjing 图层创建 1000 米缓冲区，输出名 nanjing_buffered。', 'analysis.buffer'),
    ('在 nanjing 图层新增文本字段 remark，长度 128。', 'table.add_field'),
    ('选择 nanjing 图层中 name 等于南京的要素，替换原选择。', 'selection.select_by_attribute'),
    ('将 roads 和 water 两个图层合并，输出名 merged。', 'analysis.merge'),
    ('将 nanjing_buffered 图层移至地图最下面。', 'layer.move_layer'),
    ('以 EPSG 3857 创建点图层 points_test，点坐标 x=0、y=1。', 'edit.create_point_features'),
    ('以 EPSG 3857 创建正五边形，中心 x=0、y=0，半径 10 米，起始角度 -90 度，输出名 pentagon。', 'edit.create_regular_polygon'),
]
EXPECTED_ARGUMENTS = [
    {'input_layer': 'nanjing', 'distance': {'value': 1, 'unit': 'kilometers'}, 'output_name': 'nanjing_buffered'},
    {'input_layer': 'nanjing', 'distance': {'value': 1000, 'unit': 'meters'}, 'output_name': 'nanjing_buffered'},
    {'layer': 'nanjing', 'field': {'name': 'remark', 'type': 'string', 'length': 128}},
    {'layer': 'nanjing', 'where': {'op': 'eq', 'field': 'name', 'value': '南京'}},
    {'input_layers': ['roads', 'water'], 'output_name': 'merged'},
    {'layer': 'nanjing_buffered', 'position': 'BOTTOM'},
    {'wkid': 3857, 'points': [{'x': 0, 'y': 1}], 'output_name': 'points_test'},
    {'wkid': 3857, 'center_x': 0, 'center_y': 0, 'sides': 5, 'start_angle_degrees': -90,
     'radius': {'value': 10, 'unit': 'meters'}, 'output_name': 'pentagon'},
]


def contains(actual, expected):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(key in actual and contains(actual[key], value) for key, value in expected.items())
    return actual == expected


async def main():
    async with Client(mcp) as client:
        listed = await client.list_tools()
    tools = [{'type': 'function', 'function': {'name': 'mcp__arcmap__' + item.name,
             'description': item.description, 'parameters': item.inputSchema}} for item in listed]
    schemas = {item.name: item.inputSchema for item in listed}
    persona = (ROOT / 'dsh/plugins/arcmap-agent/lib/persona.txt').read_text(encoding='utf-8')
    reports = []
    for (task, operation), expected_args in zip(CASES, EXPECTED_ARGUMENTS):
        payload = {'model': 'qwen2.5:7b-arcmap', 'temperature': 0.2, 'max_tokens': 2048,
                   'tools': tools, 'messages': [
                       {'role': 'system', 'content': persona},
                       {'role': 'user', 'content': '这是工具参数生成检查，不连接或修改地图。不调用状态、上下文或澄清工具，'
                        '只生成下面明确任务对应的一次操作工具调用，执行器不会执行它：\n' + task}]}
        request = urllib.request.Request('http://127.0.0.1:11434/v1/chat/completions',
                  data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                  headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=180) as response:
            message = json.load(response)['choices'][0]['message']
        calls = message.get('tool_calls', [])
        expected = 'mcp__arcmap__' + tool_name(operation)
        report = {'task': task, 'expected': expected, 'calls': calls, 'passed': False}
        if len(calls) == 1 and calls[0]['function']['name'] == expected:
            args = json.loads(calls[0]['function']['arguments'])
            card = _catalog.get(operation)
            verdict = check(dict(card, parameters_schema=schemas[tool_name(operation)]), args, None)
            report['public_verdict'] = verdict
            if verdict['status'] == 'proven':
                compiled = lower_arguments(args, card['parameters_schema'])
                report['runtime_verdict'] = check(card, compiled, None)
                report['matches_user_request'] = contains(args, expected_args)
                report['passed'] = report['runtime_verdict']['status'] == 'proven' and report['matches_user_request']
        reports.append(report)
        print(json.dumps(report, ensure_ascii=False), flush=True)
    output = ROOT / 'build/local-arguments-report.json'
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding='utf-8')
    if not all(report['passed'] for report in reports):
        raise SystemExit('Local model first-call acceptance failed; see ' + str(output))


if __name__ == '__main__':
    asyncio.run(main())

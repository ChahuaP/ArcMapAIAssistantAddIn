# ArcMap AI Assistant

ArcMap AI Assistant v1 现在分三层：

- `ArcMapAIAssistantAddIn`：ArcMap Python Add-in 壳，只负责原生按钮和热加载 runtime。
- `arcmap_runtime_py2`：ArcMap 内执行层，采集上下文、调用本地网关、执行已注册 ArcPy 原子操作。
- `gateway_py3` + `operation_catalog`：Python3 本地网关、DeepSeek Agentic Planner、Web 控制台、workflow 存储、工具目录。

DeepSeek 负责理解用户意图、调用本地白名单工具查询文件/上下文/能力目录，并提出 workflow；Gateway 负责校验，ArcMap 只执行 catalog 里登记过的 operation。DeepSeek 不能直接执行 ArcPy 或任意 Python。

## 普通用户使用

首次使用：

1. 打开 ArcMap。
2. 如果网页连不上，在 `ArcMap AI Assistant` 工具栏点击“启动网关”。
3. 点击“打开助手”。
4. 在打开的 Web 工作台里保存 DeepSeek API Key。
5. 在 ArcMap 工具栏点击“同步上下文”。
6. 在 Web 工作台输入自然语言请求，审批生成的 workflow。
7. 回到 ArcMap 点击“执行任务”。

ArcMap 可以通过“启动网关”手动启动本地网关，不需要用户手动开命令行。ArcMap 的“打开助手”只启动外部 launcher，不直接打开浏览器，避免 ArcMap 进程闪退。完整 AI 返回、追问、不支持原因和 workflow JSON 都在 Web 工作台查看。

`SetupDeepSeekKey.cmd` 和 `StartGateway.cmd` 只保留给开发/排障。

属性编辑类任务会直接修改原始数据。执行前 ArcMap 会统计影响范围并二次确认，用户取消则不执行。

开发/排障命令仍保留在 runtime 中：

- `/start`：手动启动本地网关。
- `/key sk-...`：在 ArcMap 里保存 DeepSeek API key。
- `/config`：打开 Web 控制台配置页。
- `/open`：打开 Web 控制台。
- `/health`：检查本地网关。
- `/execute`：执行 Web 控制台已审批的 workflow。

## 开发启动

开发时也可以手动启动本地网关：

```powershell
$env:DEEPSEEK_API_KEY='你的 key'
python -m gateway_py3
```

也可以放到：

```text
%APPDATA%\ArcMapAIAssistant\config.json
```

格式：

```json
{
  "deepseek_api_key": "你的 key",
  "model": "deepseek-chat"
}
```

当前 ArcMap Python Add-in 版本暂不支持自动添加底图。ArcMap 手工可以通过 GIS Servers 添加 WMS/WMTS；自动化底图后续需要 C# ArcObjects 或预制 `.lyr` 方案。

## Add-in 构建

```powershell
pwsh.exe -NoLogo -NoProfile -Command "python .\ArcMapAIAssistantAddIn\makeaddin.py"
```

生成文件：

```text
ArcMapAIAssistantAddIn\ArcMapAIAssistantAddIn.esriaddin
```

## 安装验证

1. 双击 `ArcMapAIAssistantAddIn\ArcMapAIAssistantAddIn.esriaddin` 安装。
2. 打开 ArcMap。
3. 如果工具栏没显示，打开 `Customize > Toolbars > ArcMap AI Assistant`。
4. 点击“启动网关”，确认本地网关可用。
5. 点击“打开助手”，外部 launcher 会打开 Web 工作台。
6. 点击“同步上下文”，把当前 ArcMap 图层、字段和选择集上传到网关。
7. 在 Web 工作台输入自然语言请求，查看 AI 返回并审批 workflow。
8. 回到 ArcMap 点击“执行任务”，执行已审批 workflow。

## 扩展原子操作

新增 operation 的固定流程：

1. 在 `operation_catalog/packs/*.json` 增加 operation spec。
2. 在 `arcmap_runtime_py2/operations/` 增加 executor。
3. 增加测试，确保 spec 和 executor 对得上。
4. 不改 DeepSeek 总 prompt；Agentic Planner 会把 operation 短索引和本地工具提供给模型，完整 schema 通过 `catalog_get_operation_schema` 查询。

## 关键点

- ArcMap Python Add-in 使用 ArcGIS Desktop 自带的 Python 2.7 运行。
- `config.xml` 负责声明 Add-in、Toolbar、Button。
- `Install/ArcMapAIAssistant_addin.py` 只负责原生按钮和热加载外部 runtime。
- `arcmap_runtime_py2/runtime.py` 负责 ArcMap 内入口。
- `gateway_py3/app.py` 负责本地网关和 Web 控制台。
- `gateway_py3/planner.py` 负责 Agentic Planner 主循环。
- `gateway_py3/agent_tools.py` 负责 DeepSeek 可调用的本地白名单工具。
- `gateway_py3/validators.py` 是 workflow 的唯一校验边界。
- `operation_catalog/packs/*.json` 负责原子操作说明。
- `gateway_py3/file_resolver.py` 负责受限本地文件查找，不做整盘索引或整盘递归扫描。
- `arcmap_runtime_py2/operations/condition_utils.py` 负责把结构化属性条件编译为 ArcGIS SQL。
- `.esriaddin` 是打包后的安装文件，本质是包含 `config.xml`、`Install/`、`Images/` 的压缩包。

# GeoPilot

GeoPilot 是一个运行在本地的 AI 工作台，专为 ArcMap（ArcGIS Desktop）设计。用户用自然语言描述 GIS 任务，系统自动生成可校验的工作流，经用户确认后在 ArcMap 内执行已注册的 ArcPy 操作。

本项目面向 **ArcMap / ArcPy**，不适用于 ArcGIS Pro。

当前版本：**1.0.0**

---

## 功能概览

- **自然语言任务规划** — 用中文或英文描述 GIS 任务，模型自动规划执行步骤。
- **ArcMap 上下文同步** — 自动读取当前地图文档的图层、字段、选择集、坐标系、MXD 保存状态和默认地理数据库。
- **工作流校验与审批** — 模型生成的每一步操作都经过操作目录校验，用户确认后才发送到 ArcMap。
- **已注册操作执行** — 只执行操作目录中已注册的 ArcPy 函数，不会运行任意 Python 代码。
- **Web 控制台** — 本地浏览器界面，覆盖对话、任务审核、模型配置、能力查看、系统诊断和任务队列。
- **语音输入** — 浏览器录音 → ASR 识别 → 文本校验，适合不方便打字的场景。
- **自定义工具构建** — 模型可以根据用户描述自动创建新的 GIS 工具，审核通过后纳入能力范围。
- **多模型供应商** — 支持 DeepSeek、MiniMax、智谱（GLM）、阿里百炼 / DashScope，可随时切换。
- **一键安装** — 打包为 Windows 安装程序，终端用户无需手动安装 Python 3。

---

## 系统架构

GeoPilot 由四个核心模块组成，各司其职：

```text
┌─────────────────────────────────────────────────────────────────────┐
│                          ArcMap (桌面应用)                            │
│                                                                     │
│  ┌────────────────────────┐      ┌────────────────────────────────┐ │
│  │    ArcMap Add-in       │      │    ArcMap Bridge (C#)          │ │
│  │  (Python 2 工具栏入口)  │      │  (通过 ArcObjects ROT 读取状态) │ │
│  └───────────┬────────────┘      └──────────────┬─────────────────┘ │
│              │                                  │                   │
│  ┌───────────▼──────────────────────────────────▼─────────────────┐ │
│  │               ArcMap Runtime (Python 2.7)                      │ │
│  │   · 上下文同步（图层/字段/选择/坐标系）                          │ │
│  │   · 工作流执行（只调用已注册的 ArcPy 操作）                      │ │
│  │   · 直写操作的二次确认（字段更新/要素删除等）                     │ │
│  └──────────────────────────┬─────────────────────────────────────┘ │
└─────────────────────────────┼───────────────────────────────────────┘
                              │ HTTP 127.0.0.1:8765
┌─────────────────────────────▼───────────────────────────────────────┐
│                   Python 3 Gateway (本地网关)                        │
│                                                                     │
│  · HTTP 服务器，承载所有 API 路由                                     │
│  · SSE 事件流（实时推送状态变化到浏览器）                              │
│  · LLM 调用（多供应商适配，工作流规划）                               │
│  · 操作目录加载与校验                                                │
│  · 自定义工具构建与审核                                              │
│  · 语音识别（ASR）与语音文本校验                                     │
│  · Web 控制台静态文件服务                                            │
└─────────────────────────────┬───────────────────────────────────────┘
                              │ HTTP / SSE
┌─────────────────────────────▼───────────────────────────────────────┐
│                      Web 控制台 (浏览器)                             │
│                                                                     │
│  · 三栏布局：侧边栏（状态/导航）| 指令画布（对话/输入）| 任务队列     │
│  · 模型配置、能力范围、系统诊断、工具管理等弹窗                       │
│  · @图层 和 #字段 提及自动补全                                       │
│  · 语音输入与文本校验面板                                            │
│  · Markdown 渲染，思考过程折叠面板                                   │
└─────────────────────────────────────────────────────────────────────┘
```

### 数据流

一次完整的任务执行流程如下：

```text
用户输入指令
    │
    ▼
Web 控制台 ──POST /runs──▶ Gateway
    │                         │
    │                    读取 ArcMap 上下文
│                    按 G0–G3 消融模式生成或接收工作流
│                    校验工作流（操作目录）并受控执行
    │                         │
│◀── 返回运行状态与工作流 ─┘
    │                                         │
    │                                    转发到 ArcMap Runtime
    │                                         │
    │                                    ArcMap 逐步执行 ArcPy 操作
    │                                         │
    │◀── SSE 事件推送执行结果 ────────────────┘
    │
    ▼
Web 控制台显示执行结果
```

---

## 安全模型

GeoPilot 的安全设计遵循 **"模型不直接执行"** 原则：

1. **模型不运行任意 Python** — 模型只能从操作目录中选择已注册的操作，不能生成自由代码。
2. **操作目录是白名单** — 网关只接受 `operation_catalog/packs/` 中定义的操作 ID。未注册的操作会被校验拦截。
3. **用户审批** — 模型生成的工作流必须经过用户在 Web 控制台确认后才发送到 ArcMap。
4. **直写操作二次确认** — 涉及字段更新、要素删除、字段删除等数据修改操作，ArcMap 会弹出额外的确认对话框。
5. **API Key 本地存储** — 密钥保存在用户目录的配置文件 `%APPDATA%\ArcMapAIAssistant\config.json`，不会上传到任何远程服务器。
6. **仅本地通信** — 网关监听 `127.0.0.1:8765`，不暴露到公网。

### 自定义工具的安全边界

当模型自动创建新的 GIS 工具时，工具构建器会执行规则检查：

- 工具必须声明明确的操作规格（operation spec）。
- 工具代码经过静态规则校验后才能提交审核。
- 工具进入"待审核"状态，用户手动启用后才会进入能力范围。
- 如果工具执行失败，用户可以请求 AI 自动修复，修复后的版本仍需重新审核。

---

## 系统要求

### 终端用户

| 项目 | 要求 |
|------|------|
| 操作系统 | Windows 10 / 11（64 位） |
| GIS 软件 | ArcGIS Desktop / ArcMap（10.1 及以上） |
| 模型供应商 | 至少一个：DeepSeek、MiniMax、智谱、阿里百炼 |
| 浏览器 | Chrome / Edge / Firefox 最新版 |
| 安装方式 | 运行 `GeoPilotSetup-1.0.0.exe` |

### 开发者

| 项目 | 要求 |
|------|------|
| Python 3 | 3.9 及以上（用于本地网关） |
| ArcGIS Desktop | 自带的 Python 2.7（用于 ArcMap Add-in 运行时） |
| PowerShell | 7.x（用于构建脚本） |
| PyInstaller | 用于打包网关可执行文件 |
| Inno Setup 6 | 用于构建安装程序 |
| .NET Framework | 4.x（用于编译 C# ArcMap Bridge） |

---

## 安装

### 从安装包安装（推荐）

1. 从 GitHub Releases 下载 `GeoPilotSetup-1.0.0.exe`。
2. 双击运行安装程序，按提示完成安装。安装程序会在需要时请求管理员权限。
3. 默认安装到 `C:\Program Files\GeoPilot`。
4. 打开 ArcMap，启用工具栏：

```text
自定义 > 工具条 > ArcMap AI Assistant
```

5. 点击工具栏上的 **「启动控制台」** 按钮，浏览器会自动打开 Web 控制台。

### 从源码开发安装

参考下方 [开发环境搭建](#开发环境搭建) 章节。

---

## 基本用法

### 第一步：启动控制台

在 ArcMap 工具栏点击 **「启动控制台」**。这会启动本地网关（如果尚未启动），并在浏览器中打开 Web 控制台。

### 第二步：配置模型

首次使用时，在 Web 控制台左侧点击 **「模型配置」**：

1. 选择半代理和全代理使用的模型。
2. 在对应供应商区域填入 API Key。
3. 点击 **「保存」**。

### 第三步：输入任务

在输入框中用自然语言描述 GIS 任务，按 **Enter** 发送。可以使用 `@` 引用图层名、`#` 引用字段名来获得自动补全。

### 第四步：审核并执行

- 半代理模式：每次生成一个工作流，确认后执行。
- 全代理模式：保持会话上下文，可以连续对话。

### 指令示例

以下是一些常见的任务描述示例：

**地图导航：**

```text
缩放到 rivers 图层
```

```text
缩放到当前选中要素
```

**要素选择：**

```text
选择 parcels 图层中 LAND_USE 等于 商业 的要素
```

```text
选择 schools 中 DISTANCE 小于 500 的要素
```

**空间分析：**

```text
给 rivers 做 200 米缓冲区，输出到 C:\GISData\Output
```

```text
把 parcels 和 flood_zone 做相交分析，输出到 C:\GISData\Output，输出名 parcel_flood
```

**数据管理：**

```text
打开 C:\GISData\Shapefiles 下所有 shp 文件
```

```text
把 roads 图层按 REGION 字段拆分导出为 shp，输出到 C:\GISData\Split
```

**数据导出：**

```text
把 parcels 当前选中的要素导出到 C:\GISData\Export，输出名 parcels_selected
```

```text
把当前地图导出为 PDF，保存到 C:\GISData\Maps，文件名 city_map
```

**属性表操作：**

```text
给 buildings 添加一个文本字段 REMARK，长度 100
```

```text
计算 parks 的 AREA 字段，用 SHAPE_AREA 的值填充
```

---

## 两种代理模式

GeoPilot 提供两种代理强度，适用于不同场景：

### 半代理模式

- 每次对话独立处理，不保留上下文。
- 适合单次明确任务：缩放到图层、选择要素、做一次缓冲区分析等。
- 生成的工作流一次性展示，确认后执行。

### 全代理模式

- 保持会话上下文，支持连续对话和多轮修改。
- 适合复杂任务：先查看图层信息，再根据结果决定下一步操作。
- 可以追问、修改、撤回之前的指令。

在 Web 控制台顶部的模式栏中切换模式。

---

## Web 控制台

Web 控制台是一个三栏布局的本地 Web 应用：

```text
┌──────────┬────────────────────────────┬──────────────┐
│  侧边栏   │        指令画布             │   任务队列    │
│          │                            │              │
│ · 系统状态 │ · 模式切换（半代理/全代理） │ · 任务卡片    │
│ · 功能导航 │ · 对话记录（Markdown 渲染） │ · 执行步骤    │
│          │ · 输入框 + 语音 + 发送       │ · 技术详情    │
│          │                            │ · 操作按钮    │
└──────────┴────────────────────────────┴──────────────┘
```

### 侧边栏功能

| 按钮 | 功能 |
|------|------|
| 能力范围 | 查看当前已注册的全部 GIS 操作，按类别分组 |
| 地图快照 | 查看最近一次同步的 ArcMap 上下文：图层列表、坐标系、MXD 状态 |
| 工具管理 | 审核和管理自定义工具（启用 / 拒绝 / 删除） |
| 系统诊断 | 检查安装、配置、目录、版本和网络状态 |
| 模型配置 | 选择模型、填写 API Key、配置接口地址 |

### 提及自动补全

在输入框中使用特殊前缀触发自动补全：

- `@` — 弹出图层列表，按名称过滤。选择后插入图层引用。
- `#` — 弹出字段列表。如果前面已有 `@图层名`，则只显示该图层的字段。

### 语音输入

1. 点击输入框旁的 **「语音」** 按钮开始录音。
2. 说完后再次点击停止。
3. 系统自动识别语音并填入输入框。
4. 点击 **「校验文本」** 可以让模型修正识别结果中的 GIS 术语错误。

---

## API Key 配置

### 安全原则

API Key 是用户本地运行时配置，不是源代码的一部分。

- Web 控制台将 Key 写入 `%APPDATA%\ArcMapAIAssistant\config.json`。
- 配置接口只返回 Key 状态（已保存 / 未配置），不会返回原始 Key 值。
- 本地密钥文件（`.env`、`config.json`、`*.local.json`、`*secrets*.json`、`*api_keys*.json`）已被 Git 忽略。
- **不要**在 README 示例、测试代码、操作目录或源代码中放置真实 API Key。

### 支持的供应商

| 供应商 | 环境变量 | 说明 |
|--------|----------|------|
| DeepSeek | `DEEPSEEK_API_KEY` | DeepSeek V3 / R1 系列模型 |
| MiniMax | `MINIMAX_API_KEY` | MiniMax 模型 |
| 智谱 | `ZHIPU_API_KEY` | GLM 系列模型 |
| 阿里百炼 | `DASHSCOPE_API_KEY` 或 `BAILIAN_API_KEY` | 通义千问系列模型 |

阿里百炼还支持 Token Plan API Key，运行时优先级如下：

```text
providers.qwen.token_plan_api_key                    （最高优先级）
BAILIAN_TOKEN_PLAN_API_KEY / DASHSCOPE_TOKEN_PLAN_API_KEY
providers.qwen.api_key
DASHSCOPE_API_KEY / QWEN_API_KEY / BAILIAN_API_KEY   （最低优先级）
```

也可以在 Web 控制台的模型配置中直接填写 Key，保存后优先级高于环境变量。

---

## 开发环境搭建

### 1. 克隆仓库

```powershell
git clone https://github.com/your-org/geopilot.git
cd geopilot
```

### 2. 安装 Python 3 依赖

```powershell
pip install pyinstaller
```

项目网关核心不依赖第三方运行时库，全部使用 Python 标准库。

### 3. 启动网关

设置至少一个模型供应商的环境变量，然后启动网关：

```powershell
# 选择其中一个（或多个）
$env:DEEPSEEK_API_KEY = "sk-your-deepseek-key"
$env:DASHSCOPE_API_KEY = "sk-your-bailian-key"
$env:BAILIAN_TOKEN_PLAN_API_KEY = "your-token-plan-key"
$env:MINIMAX_API_KEY = "your-minimax-key"
$env:ZHIPU_API_KEY = "your-zhipu-key"

python -m gateway_py3
```

网关启动后监听 `http://127.0.0.1:8765`，在浏览器中打开即可使用 Web 控制台。

### 4. 构建 ArcMap Add-in

```powershell
python .\ArcMapAIAssistantAddIn\makeaddin.py
```

生成的 Add-in 文件：

```text
ArcMapAIAssistantAddIn\ArcMapAIAssistantAddIn.esriaddin
```

双击 `.esriaddin` 文件安装到 ArcMap，然后重启 ArcMap。

### 5. 编译 C# ArcMap Bridge

ArcMap Bridge 是一个 C# 外部程序，通过 ArcObjects ROT 读取 ArcMap 状态。使用 Visual Studio 或 MSBuild 编译：

```powershell
msbuild ArcMapBridgeExternal\ArcMapBridgeExternal.csproj /p:Configuration=Release
```

编译产物复制到 ArcMap Add-in 目录下供运行时调用。

### 6. 运行测试

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
```

测试覆盖：版本号对齐、操作目录校验、工作流验证、路径处理等。

---

## 构建发布包

从仓库根目录执行：

```powershell
pwsh.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File .\packaging\build_release.ps1 -BuildGateway -BuildInstaller
```

构建流程会自动完成以下步骤：

1. 用 PyInstaller 将 Python 3 网关打包为独立可执行文件。
2. 复制运行时文件到暂存目录。
3. 写入 `VERSION` 文件。
4. 调用 Inno Setup 编译安装程序。

最终产物：

```text
release\
  GeoPilotSetup-1.0.0.exe        ← Windows 安装程序
  geopilot-arcmap\                ← Agent 集成包（SKILL.md 等）
```

---

## 项目结构

```text
geopilot/
│
├── ArcMapAIAssistantAddIn/     ArcMap Python Add-in 外壳
│   ├── config.xml              Add-in 配置（版本、工具栏定义）
│   ├── ArcMapAIAssistant_addin.py  工具栏按钮事件处理
│   └── makeaddin.py            Add-in 打包脚本
│
├── ArcMapBridgeExternal/       C# ArcMap Bridge 程序
│   ├── Program.cs              入口：通过 ArcObjects ROT 连接 ArcMap
│   └── Properties/
│       └── AssemblyInfo.cs     程序集版本号
│
├── arcmap_runtime_py2/         ArcMap 端 Python 2 运行时
│   ├── runtime.py              运行时主循环（上下文同步 + 工作流执行）
│   ├── context_reader.py       读取 ArcMap 上下文（图层、字段、坐标系）
│   ├── gateway_client.py       与网关通信的 HTTP 客户端
│   ├── workflow_executor.py    工作流执行引擎
│   ├── config_manager.py       运行时配置管理
│   └── operations/             已注册的 ArcPy 操作执行器
│       ├── view_layer.py       视图与图层操作
│       ├── selection.py        选择操作
│       ├── analysis.py         空间分析操作
│       ├── table.py            属性表操作
│       ├── export.py           导出操作
│       ├── map_context.py      地图上下文操作
│       ├── data_management.py  数据管理操作
│       ├── edit_geometry.py    几何编辑操作
│       └── layout.py           布局操作
│
├── gateway_py3/                本地 Python 3 网关
│   ├── app.py                  HTTP 服务器入口
│   ├── __main__.py             命令行启动入口
│   ├── experiments.py          G0–G3 实验规划与审计链
│   ├── run_controller.py       运行生命周期与受控执行器
│   ├── llm_providers.py        多供应商 LLM 适配层
│   ├── catalog_loader.py       操作目录加载器
│   ├── validators.py           工作流校验器
│   ├── workflow_store.py       工作流持久化存储
│   ├── tool_builder.py         自定义工具构建器
│   ├── voice.py                语音识别与文本校验
│   ├── diagnostics.py          系统诊断检查
│   ├── event_bus.py            SSE 事件流服务
│   ├── arcmap_bridge_client.py ArcMap Bridge 通信客户端
│   ├── routes/                 API 路由
│   │   ├── runs.py             /runs 运行路由
│   │   ├── arcmap.py           /arcmap/* 路由
│   │   ├── common.py           /health, /config 等公共路由
│   └── web/                    Web 控制台前端
│       ├── index.html          主页面（三栏布局）
│       ├── tokens.css          设计令牌（颜色、字体、间距）
│       ├── styles.css          基础样式（布局、按钮、表单）
│       ├── components.css      组件样式（气泡、卡片、弹窗）
│       ├── app.js              核心逻辑（状态、API、配置）
│       ├── app_render.js       渲染逻辑（对话、任务、Markdown）
│       ├── app_mentions.js     @提及补全 + 事件流 + 工具函数
│       └── app_voice.js        语音输入与文本校验
│
├── operation_catalog/          GIS 操作目录
│   └── packs/                  按类别组织的操作规格
│       ├── analysis.json       空间分析（缓冲区、裁剪、相交、融合…）
│       ├── selection.json      选择（属性查询、空间查询、导出选中…）
│       ├── view_layer.py       视图与图层（缩放、可见性、定义查询…）
│       ├── table.json          属性表（添加字段、计算字段…）
│       ├── export.json         导出（地图 PDF/PNG、表 CSV、图层 KML…）
│       ├── map_context.json    地图上下文（保存 MXD、刷新…）
│       ├── data_management.json 数据管理（打开文件、拆分导出…）
│       ├── edit_geometry.json  几何编辑（编辑要素…）
│       └── layout.json         布局操作
│
├── agent_integrations/         外部 Agent 集成
│   └── geopilot-arcmap/
│       └── SKILL.md            Agent Skill 描述文件
│
├── packaging/                  构建与安装
│   ├── build_release.ps1       一键构建脚本
│   ├── install.ps1             安装辅助脚本
│   ├── GeoPilotSetup.iss       Inno Setup 安装程序配置
│   └── uninstall.iss           卸载脚本
│
├── tests/                      测试套件
│   └── gateway/
│       ├── test_versions.py    版本号对齐检查
│       └── ...                 其他测试
│
└── LICENSE                     MIT 许可证
```

---

## 添加新操作

操作目录采用 **描述与执行分离** 的设计：模型只能看到操作描述，ArcMap 只执行已注册的执行器函数。

要添加一个新的 GIS 操作：

### 1. 定义操作规格

在 `operation_catalog/packs/` 下对应的类别文件中添加操作规格：

```json
{
  "id": "analysis.near_analysis",
  "version": "1.0",
  "summary": "对输入要素执行邻近分析，找出最近的要素并计算距离",
  "parameters": [
    {
      "name": "input_features",
      "type": "layer",
      "required": true,
      "description": "输入要素图层"
    },
    {
      "name": "near_features",
      "type": "layer",
      "required": true,
      "description": "邻近要素图层"
    },
    {
      "name": "search_radius",
      "type": "linear_unit",
      "required": false,
      "description": "搜索半径，例如 500 Meters"
    }
  ]
}
```

### 2. 实现执行器

在 `arcmap_runtime_py2/operations/` 下添加对应的 ArcPy 执行函数：

```python
def execute_near_analysis(params, context):
    """执行 Near 分析。"""
    import arcpy
    input_features = params["input_features"]
    near_features = params["near_features"]
    search_radius = params.get("search_radius", "")
    arcpy.Near_analysis(input_features, near_features,
                        search_radius=search_radius)
```

### 3. 注册执行器

在运行时的操作注册表中将操作 ID 映射到执行函数。

### 4. 添加测试

为操作规格、校验逻辑和执行器行为编写测试。

### 5. 重新构建

按需重新构建 Add-in 或发布包。

---

## 外部 Agent 集成

外部编排器通过同一组 `/runs` API 提交任务或严格的结构化产物；所有提交都经过相同的审计、校验、权限与执行链：

```text
POST /runs                   创建运行（可选自动执行）
GET  /runs/{id}              查询运行状态与结果
POST /runs/{id}/cancel       协作式取消运行
GET  /runs/report            导出可复现实验报告（可按 mode 筛选）
```

Agent 集成配置文件位于 `agent_integrations/geopilot-arcmap/SKILL.md`，定义了 Agent 可以调用的能力和参数。

---

## 已知限制

| 限制 | 说明 |
|------|------|
| 底图自动化 | ArcMap 可以通过 GIS Servers 手动添加 WMS/WMTS，但自动底图操作需要后续基于 ArcObjects 或 `.lyr` 文件实现。 |
| 多 ArcMap 窗口 | 打开多个 ArcMap 窗口时，Web 控制台可能无法准确判断目标窗口。建议保持单个 ArcMap 窗口打开，或使用外部 Agent CLI 的 `arcmap-list` 和 `arcmap-select --hwnd <hwnd>` 指定目标。 |
| 仅 ArcMap | 本项目面向 ArcMap 和 ArcPy，不适用于 ArcGIS Pro。 |
| 仅本地 | 网关和 Web 控制台运行在 `127.0.0.1`，不支持远程访问。 |

---

## 许可证

MIT License. 详见 [LICENSE](LICENSE)。

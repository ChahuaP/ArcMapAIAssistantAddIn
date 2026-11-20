# 知识与资料

规智在成果目录的 knowledge 子目录保存原件、资料版本、检索元数据和引用位置。索引及文档解析通过本机服务完成，应让三个服务只监听回环地址。使用在线回答模型时，DSH 可将检索命中的原文片段带入模型上下文。

## 准备本机服务

- [Docling Serve](https://github.com/docling-project/docling-serve) 提供文档结构识别和 OCR。本版验收环境使用 Docling Serve 1.38.2；安装后用 `docling-serve run --host 127.0.0.1 --port 5001` 启动。Windows 下可设置 `PYTHONUTF8=1`。中文扫描件需要在 Docling 环境安装 ONNX Runtime 与 RapidOCR。首次解析前需准备布局与 OCR 模型。
- [Qdrant](https://github.com/qdrant/qdrant/releases) 保存向量。本版验收环境使用 Qdrant 1.18.0。Windows 可使用官方 x64 可执行文件，将 `QDRANT__SERVICE__HOST` 设为 `127.0.0.1`，HTTP 端口为 6333。将数据目录配置为本机较短的独立路径，定期与成果目录一同备份。
- [Ollama](https://ollama.com/library/bge-m3) 为资料与问题生成向量。启动服务并运行 `ollama pull bge-m3`，默认端口为 11434。

## 导入与核对

在「设置 → 知识与资料」保存本机向量模型，选择文档，并按需填写适用区域、版本或文号、生效和失效日期。支持 30 MB 以内的 PDF、DOCX、TXT 和 Markdown。资料可编辑登记信息、保存原件或删除；删除会同步移除向量。更换向量模型前需要清空资料并重新导入，以保持向量一致性。

「检索验证」可核查返回的原文片段和定位。对话中的 DSH 工具搜索默认按当天核对有效期；用户可指定地区和历史日期。地区使用登记时填写的名称，日期格式为 YYYY-MM-DD。资料作为证据处理，文档内出现的操作指令不构成授权。

本机服务、模型和它们的运行环境独立安装。使用单位材料前，确认允许在线回答模型接收检索命中的资料片段。

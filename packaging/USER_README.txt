ArcMap AI Assistant 使用说明
===========================

一、安装

1. 关闭 ArcMap。
2. 双击 InstallArcMapAIAssistant.cmd。
3. 按提示选择安装位置。
   推荐安装到 D:\ArcMapAIAssistant。
4. 安装完成后打开 ArcMap。
5. 如果没有看到工具栏，请在 ArcMap 菜单打开：
   Customize > Toolbars > ArcMap AI Assistant


二、使用

1. 在 ArcMap 工具栏点击“启动AI后台”。
2. 点击“显示控制台”，会打开网页控制台。
3. 第一次使用时，在网页右上角打开 Key 配置，填写 DeepSeek API Key 并保存。
4. 回到 ArcMap，点击“同步上下文”。
5. 在网页控制台输入要做的 GIS 操作。
6. 网页生成任务后，点击确认发送到 ArcGIS。
7. 回到 ArcMap，点击“执行工作流”。

如果你新增、删除、重命名了图层，或者改变了选择集，请重新点击“同步上下文”。


三、示例指令

- 缩放到 nanjing 图层
- 选择 nanjing 中 NAME 等于 鼓楼区 的要素
- 给 roads 做 100 米缓冲区，输出到 D:\Data
- 打开 D:\Data\shapefile 下所有 shp
- 把 nanjing 当前选中的要素导出到 D:\Data，输出名 nanjing_selected
- 把 nanjing 图层按 NAME 字段拆分导出为 shp，输出到 D:\Data


四、卸载

1. 关闭 ArcMap。
2. 双击 UninstallArcMapAIAssistant.cmd。
3. 输入 Y 确认。

卸载会删除 ArcMap 插件和程序安装目录。
DeepSeek API Key 默认保留，重新安装后还能继续使用。

# 项目事实与操作入口

`project-spec.json` 是已确认需求的唯一维护位置；清单、合同、采集配置和原始证据继续使用原格式。先区分操作 `create/modify/organize/audit`，再记录设计模式 `replicate/adapt/original`。已有确认直接复用，只问缺失或冲突项。审核只读，不能把修复含在审核命令中。

## 开局

按[项目事实模板](project-spec.example.json)填写事实。没有确认的值用 `null`、空数组或 `false`，不猜尺寸、名称、字体、权限或确认记录。尺寸与屏形使用正式校验器的 `screen_spec`：480×480圆形、466×466圆形、390×390圆形、390×450圆角矩形；其他规格先报告需要适配，禁止偷偷改为480×480。圆角参数与安全区按[多尺寸规则](multi-size.md)。

```bash
python3 "<SKILL_DIR>/scripts/watch_face.py" init <项目目录> --input <需求.json> --json
python3 "<SKILL_DIR>/scripts/watch_face.py" preflight <项目目录> --figma-evidence figma-preflight.json --json
```

使用安装了Pillow的Python，或 `uv run --with pillow python`。本地检查与Figma现场检查分别列出；连接、字体或权限无真实证据时为 `unverified`。新建不要求尚未建立的Active/AOD/缩略图ID。`create`走完整工作流；`modify/organize`按变更影响接续，需求变化的合同门禁重新置为pending；`audit`永不允许写入。

现场证据来自Figma只读采集，字段为 `origin:figma_readonly`、`capture_signature`、`file_key`、`target_id`、`nodes`、`fonts`、`source_fonts`、`libraries`、`permissions`。字体必须有实际family/style；原生TEXT样式来源登记 `source_fonts[节点ID]`。声明的组件库逐个记录 `key` 与真实 `accessible` 结果。不能因读到目标而写 `write:true`。

写权限依据只能复用已有成功写入或建文件响应，不试写节点探测权限。将工具返回JSON内容原样保存到项目内，`permissions.basis`登记 `kind:successful_write|file_creation`、`file_key`、`receipt_path`、文件真实SHA-256。成功写入响应需含 `changedNodeIds`，basis内 `changed_node_ids`逐项相同；建文件响应需含 `file_key/file_url`。`confirmations.write_scope:true`和 `scope.node_ids`来自用户已授权的范围。审核只需要读取证据。

## 同步与旧项目

```bash
python3 "<SKILL_DIR>/scripts/watch_face.py" import <旧项目目录> --manifest manifest.json --operation modify --json
python3 "<SKILL_DIR>/scripts/watch_face.py" sync <项目目录> --json
python3 "<SKILL_DIR>/scripts/watch_face.py" status <项目目录> --json
python3 "<SKILL_DIR>/scripts/watch_face.py" check <项目目录> --gate prototype --json
python3 "<SKILL_DIR>/scripts/watch_face.py" check <项目目录> --json
```

`import`只新增事实和同步基线，不改旧文件，不从尺寸正确推断用户确认。跨文件尺寸和来源策略冲突需要在project-spec中明确选值，并记录对应的 `confirmations.target/source_strategy:true`后再同步；未解决时整个同步停止。旧能力模式和来源限制完整保留。未知显示名称不抹掉旧切图前缀。

`sync`显示修改字段，只更新尺寸、能力选择、来源策略、名称前缀和目标ID等需求派生项。人工编辑了已维护字段且与事实冲突时整次停止；测量、数量、PNG哈希、导出设置、显示节点ID和验收结论不属于同步内容。重复运行零改动。`project-state.json`保存同步基线与过期项，不能充当证据或确认来源。

事实变化标记旧证据过期并重置合同门禁，原快照、报告和导出回执保留。`status`只读；合同标签或视觉通过不能证明生产通过。当前报告必须绑定现有文件哈希，并重新调用原正式校验器。统一入口的默认证据文件为 `snapshot.json/evidence.json/visual-review.json/report.json`，PNG目录为 `exports`；已有项目采用其他名称时保留原文件，通过 `status/check --snapshot <原快照> --evidence <原回执> --visual-review <原视觉记录> --report <原报告> --exports-dir <原PNG目录>`显式传参。

退出码统一：`0`可继续，`1`检查未通过或事实待确认，`2`输入结构无效。初始化成功仅表示建立待完成模板，不代表允许写入Figma或完成制作。

# 项目事实与操作入口

`project-spec.json` 是已确认需求的唯一维护位置；清单、合同、采集配置和原始证据继续使用原格式。先区分操作 `create/modify/organize/audit`，再记录设计模式 `replicate/adapt/original`。已有确认直接复用，只问缺失或冲突项。审核只读，不能把修复含在审核命令中。

## 开局

按[项目事实模板](project-spec.example.json)填写事实。没有确认的值用 `null`、空数组或 `false`，不猜尺寸、名称、字体、权限或确认记录。尺寸与屏形使用正式校验器的 `screen_spec`：480×480圆形、466×466圆形、390×390圆形、390×450圆角矩形；其他规格先报告需要适配，禁止偷偷改为480×480。圆角参数与安全区按[多尺寸规则](multi-size.md)。

```bash
python3 "<SKILL_DIR>/scripts/watch_face.py" init <项目目录> --input <需求.json> --json
python3 "<SKILL_DIR>/scripts/watch_face.py" preflight <项目目录> --figma-evidence figma-preflight.json --json
```

使用安装了Pillow的Python，或 `uv run --with pillow python`。本地检查与Figma现场检查分别列出；连接、字体或权限无真实证据时为 `unverified`。新建不要求尚未建立的Active/AOD/缩略图ID。`create`走完整工作流；`modify/organize`按变更影响接续，需求变化的合同门禁重新置为pending；`audit`永不允许写入。

`init`允许目录中已有需求输入、参考素材和建文件回执，并保留原始字节；已有项目事实、合同、清单、采集配置或验证证据（含其临时文件）时拒绝初始化，使用`import`或既有项目命令，不覆盖历史记录。显式`--operation`仅覆盖待写入事实，不改原需求文件；`audit`在写入前停止。初始化仍只建立待完成模板，不提升任何门禁。

现场证据来自Figma只读采集，字段为 `origin:figma_readonly`、`capture_signature`、`file_key`、`target_id`、`nodes`、`fonts`、`source_fonts`、`libraries`、`permissions`。字体必须有实际family/style；原生TEXT样式来源登记 `source_fonts[节点ID]`。声明的组件库逐个记录 `key` 与真实 `accessible` 结果。不能因读到目标而写 `write:true`。

用户授权与技术权限分别判断：`confirmations.write_scope:true`及非空`scope.node_ids`记录用户授权；`permissions`只表示已证实的技术权限。现场目标、读取权限、字体、组件库与范围满足，且没有明确写入拒绝或失效回执时，允许在授权范围内执行首次必要制作，`write_ready:true`，但`figma.permissions`和`permissions`仍为`unverified`。不得为了探测权限试写节点；第一次动作必须直接产出本次制作所需内容。成功后保存真实响应，再预检验证技术权限；失败或明确`write:false`时停止，保留真实失败事实。

已有写权限依据继续复用成功写入或建文件响应，不能因回执失效退回首次制作通道。将工具返回JSON内容原样保存到项目内，`permissions.basis`登记 `kind:successful_write|file_creation`、`file_key`、`receipt_path`、文件真实SHA-256。成功写入响应需含 `changedNodeIds`，basis内 `changed_node_ids`逐项相同；建文件响应需含 `file_key/file_url`。`confirmations.write_scope:true`和 `scope.node_ids`来自用户已授权的范围。审核只需要读取证据。

合同处于`needs_stronger_model_or_human_asset`时优先停止正式制作：无论技术写权限是否已证实，`write_ready/routing.write_allowed`均为false，检查返回1并提示只读等待；首次必要制作不能覆盖此停止条件。普通pending门禁不等于全局停止，阶段制作仍按现场条件判断。

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

删除此前由同步维护的`capability_presentations`时，只有实际值仍与旧owned基线一致才清理；外部修改仍按冲突停止整次同步。旧导入项目从未维护此字段时不新增或删除其显示含义。

事实变化标记旧证据过期并重置合同门禁，原快照、报告和导出回执保留。`status`只读；合同标签或视觉通过不能证明生产通过。当前报告必须绑定现有文件哈希，并重新调用原正式校验器。统一入口的默认证据文件为 `snapshot.json/evidence.json/visual-review.json/report.json`，PNG目录为 `exports`；已有项目采用其他名称时保留原文件，通过 `status/check --snapshot <原快照> --evidence <原回执> --visual-review <原视觉记录> --report <原报告> --exports-dir <原PNG目录>`显式传参。

退出码统一：`0`可继续，`1`检查未通过或事实待确认，`2`输入结构无效。初始化成功仅表示建立待完成模板，不代表允许写入Figma或完成制作。

`preflight/check/status`共享`phase_state`，并在顶层保留同名字段：`stage`、原始`gates`、`write_ready`、实际`permissions`、`production_validation/production_ready`、`blocker_details`及`next_step`。原有`routing/local_checks/figma_checks/blockers`等返回字段保持兼容。`write_ready`只说明现场制作条件满足，不能证明阶段已通过或正式交付完成。阶段卡同时运行当前阶段的真实只读门禁判定，保留原始pending，不写pass。阻塞项分为`missing_user_fact`（缺用户事实）、`implementation_error`（实现或制作未完成）、`stale_evidence`（缺失或过期证据）、`unsupported`（暂不支持），每项给出具体下一步。需求与同步基线或合同不一致时先`sync`；不能用满足现场条件绕过。

`check --gate`与正式`check/status`都会核对已确认的目标、来源策略、开发依据和合同需求派生字段；state与事实相同不代表替换进来的旧合同仍有效。阶段门禁只证明阶段条件，保持只读；命令成功不会写入门禁或授权。写入前查看阶段卡的`write_ready`和现场条件。缺产品码及实际设备约束时仍不能正式通过；这些外部事实不阻断当前阶段制作与评审，不生成占位码或默认设备事实。设备保留区沿用采集配置的`canvas.reserved_regions`及正式校验器规则。

样稿pass必须有[阶段卡片](stage-cards.md)规定的封存证据；check同时复核其合同需求、项目事实、快照、PNG和逐项评审。原样稿封存目录不能被后续组装覆盖，缺失证据不自动补为通过。

正式`check`成功后保存校验器实际生成的当前报告，替换前将旧报告原始字节留在报告所在目录的`history/<报告名>-<SHA256>.json`。`status`根据当前输入哈希与重新校验结果判断，不改文件。`audit`的`check`只返回本次结果，不保存报告；后续`status`仍需可用的当前报告。

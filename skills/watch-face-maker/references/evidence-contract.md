# 表盘原始证据契约

## Figma 快照采集

在 `use_figma` 的只读 JavaScript 上下文调用 `await collectSnapshot(config)`。`config` 必须包含 `file_key`、`section_id`、`asset_board_id`、`main_id`、`active_id`、`aod_id`、`preview_id`；可用 `offset` 和 `limit` 分页，`limit` 默认为 20。每页返回 `page: {offset, limit, count}`、`total_nodes` 和 `next_offset`。节点过多时可设 `skip_instance_children: true`，此时仍记录实例本身和母件引用，报告会把深层可见叶子组装标为未验证。

合并契约：所有页必须具有相同 `total_nodes`、`capture_signature` 和遍历选项，`page.offset` 从 0 按 `page.count` 无缝连续，末页 `next_offset` 为 `null`。采集器每页计算整组几何签名，即使节点数量相同，也能识别字体、位置或导出设置变化。使用 `scripts/merge_snapshot_pages.py PAGES.json SNAPSHOT.json` 合并按顺序保存的分页数组；签名变化、缺页或重复ID时停止，不写伪完整结果。不能通过重新填写总数或去重来掩盖版本混合。导出完成后再核对签名；变化时重新采集和验证受影响产物。合并后添加 `export_items: [{"node_id":"...", "path":"SampleFace/...png", "role":"asset|presentation_main|aod_full"}]`，路径从当前节点名称生成。

采集器只遍历指定 section，不加载字体，不写节点，不发网络请求。`glyph_bounds` 是 Figma `absoluteRenderBounds` 的记录，不是 PNG alpha bounds。若字形的祖先被裁切，必须从临时无裁切测量源补入 `unclipped_glyph_bounds`，否则对应尺寸检查为 `unverified`。分页合并后可人工补充：

- `roles: {"time_main_id": "...", "time_aod_id": "..."}`
- `aod_style: {"node_name_prefix": "SampleFace/Num/Time-Dark/", "stroke_color": "#B3B3B3"}`：采集 TEXT 的 `fills`、`strokes`、`stroke_weight`、`stroke_align`；同时检查母件与 AOD 显示文字。旧快照缺少样式字段时为未验证，不能仅凭 Time-Dark 名称推定是描边。
- `shared_data_policy: {"prefix": "SampleFace", "preserved_families": []}`：按实际母件字符、字号和样式核对共享数据族；单档使用Data，双档使用Large/Small且字号严格递减。`preserved_families`仅记录用户明确暂不调整的旧专用字模，须在项目需求中解释，不作为新项目默认排除名单。
- `resource_families: [{"name": "...", "kind": "time|data|date|unit|symbol", "node_ids": ["..."]}]`
- 同一共享数据族的`node_ids`必须包含对应符号和单位；`shared_data_policy.symbol_resources`记录`node_id`、实际`character`与目标`path`，检查遗漏改名或漏纳入统一高度的资源。`vertical_padding`默认0，按完整字形确定最小高度；旧族确需保留额外上下余量时显式记录该值与项目理由。
- `state_families: [{"name": "weather", "node_name_prefix": "SampleFace/Weather/", "expected_count": 48, "start_index": 0, "index_width": 3}]`
- `animations: [{"node_ids": ["..."], "expected_count": 10, "start_index": 0}]`

`source_only_node_ids` 记录已合入背景或作为旧版对照保留、不再交付的母件，项目中逐项注明原因。它们必须仍在资产板上并关闭PNG导出；从正式导出映射及对应文字资源族中移除，并清理导出目录里的旧独立 PNG。不能因为展示稿未引用某个合法状态就将其排除。描边后的字形渲染边界也必须完整，不能继续沿用实心字的旧测量。

最小宽度按完整字形宽度向上取4倍数，不机械追加2px。还须验证该宽度内存在合法的0.1px文本位置：若字体侧边距使所有可用位置都会切边，保留下一档并记录具体原因。四边实际余量和PNG另行核对，不能用固定留白公式代替检查。

`state_families` 的实际数量、序号连续性和重号均由资产板母件名称按 `node_name_prefix` 推导，不接受人工 `node_ids` 数量作为通过证据；匹配的每个节点还必须由 `export_items` 正式导出。未提供 `state_families` 时该项为 `unverified`。

## PNG 文件

正式导出文件路径以 `export_items.path` 为准，尺寸从对应节点 `export_settings` 的 `SCALE/WIDTH/HEIGHT` 约束推导。主展示图必须是 `Presentation/Main.png`。真实 PNG 检查依赖 Pillow：

已有 Figma JavaScript 能力时，小切图可按节点现有 `exportSettings` 批量调用 `exportAsync`，再用 `base64Encode` 传回并还原文件；分批控制响应体积，避免截断。整幅大图沿用下载工具。实际节点名称决定文件路径，用户更名或合并资源后必须重新读取映射，不能继续按旧路径覆盖文件。

```bash
uv run --with pillow python "<SKILL_DIR>/scripts/validate_manifest.py" MANIFEST.json \
  --snapshot SNAPSHOT.json --exports-dir EXPORTS --report REPORT.json
```

报告状态仅为 `pass`、`fail`、`unverified`、`not_applicable`。退出码 0 仅表示已执行的自动检查没有失败；人工视觉、背景语义、用户确认和真机验收始终单独记录。

用户要求清除本地切图时，先完成文件检查，再清除本轮导出目录及临时预览。报告保留尺寸、哈希、检查时间和清理记录，并明确文件已经删除、复验需要重新导出。清理不得删除用户提供的参考图、背景源素材或 Figma 组件，也不得改写既有 Git 历史。

- `integer_positions: true`：从资产母件及主稿/AOD/预览的原生可见层级检查局部X/Y，遇实例停止深入并回到源母件检查。启用text_geometry时所有文字母件的原生文本允许一位小数，其余布局仍要求整数；浮点尾差容差为1e-5，不允许用显示格式化伪造精度。
- `vector_artwork_roots: [{"node_id":"...", "source":"用户确认的图标来源"}]`：仅用于保留原有矢量图标内部造型。根节点必须是FRAME、说明来源且后代不含TEXT；根本身仍检查整数位置，内部路径不强制取整。不得把功能布局、整个表盘或文字资源列入此项以绕过检查。背景内刻度文字属于整幅图案，独立文字切图则按纯TEXT/FRAME/GROUP母件识别。
- `digit_geometry: true`：保留旧版0–9数字专项检查，不能替代整组文字检查。
- `text_geometry: true`：当前制作必设。按原生TEXT识别完整文字切图，检查文本框宽高为正偶数整数、X/Y最多一位小数、实际字形无切边和居中（每轴0.05px取整偏差，另容许0.001px尾差）；包括符号单位、星期月份和标签。单字符标点仅自动检查水平中心，其垂直基线另附原始节点对应证据。缺少此声明或测量不足时为未验证。母件宽高4倍数规则不变。
- `progress_resources: [{"prefix":"SampleFace/Progress/Pace/","shape":"irregular","include_track":true,"segment_count":8,"static_track_name":"Static/Progress/Pace/Track"}]`：当前分段进度检查读取原始VECTOR、visible、opacity、fills；确认所有状态保留完整分段并移除背景中重复的旧底。形态允许regular/irregular/battery；此分段计数检查仅用于声明了segment_count的序列，连续电量外壳按其实际结构核对。真实PNG再比较轮廓及亮起段，不能仅凭include_track自报。

## 同轮来源绑定与断点（v2）

复用现有采集器与Python校验器，不新增服务。采集器v2在异步读取前后分别核对内容及母件引用，覆盖文字分段样式、矢量路径、图片哈希、导出设置与布局，以及混合模式、遮罩、布尔运算、描边端点/虚线、独立圆角、圆弧和实际变量模式。渲染属性必须同时参与整组签名和资源指纹，不能只采集尺寸与填色；更新采集器后重新采集，不把旧快照补写成新证据。`stability.before/after` 必须等于 `capture_signature`。该签名用于发现变化，不等同于Figma服务器修订号，也不是视觉验收。

- 先执行本地规则/名称检查，再做目标节点轻量预检。只读名称数量时不要采集整个矢量树。
- 分页同时受 `limit` 和 `max_bytes` 限制；默认最多20节点、约12000字节节点正文，预留响应元数据空间。始终使用返回的 `next_offset`，不能按固定20递增。
- `profile: "preflight"` 用于修正前预检，不读取图片源尺寸，不能建立正式导出证据。`profile: "validation"` 是稳定后的常用模式：保留实际检查字段，去掉重复别名及原始大矢量正文，原始矢量和样式仍参与整组签名及每节点 `content_signature`。需要诊断具体路径时才用 `full`（旧默认）。不同模式的分页不能混合。精简模式不减少校验器已有检查范围，也不把内容签名当作视觉证明。
- 签名复核只需返回签名时可设 `profile: "preflight", limit: 1`，不拼完整快照。其整组签名仍遍历同一分组，并须沿用相同 `skip_instance_children`；不能把这个单页当成完整节点证据。
- 单节点超过预算会明确报错；改为单独读该节点所需字段，不能静默截断或丢掉矢量。对外返回总量仍需根据工具响应上限控制。重复图片源尺寸在单页内复用。
- 分页合并拒绝缺页、重叠、不同采集器版本和采集前后漂移。旧v1快照可以读取，但稳定性显示“未验证”。
- `run_evidence.py` 提供同轮记录、增量导出计划及安全清理；这些是现有工具流程调用的本地辅助函数，不会自行调用Figma。

流程：

```python
from run_evidence import start_run, record_export, checkpoint

run = start_run(requirements, snapshot)  # 传入本轮有效需求与合并后的v2快照
checkpoint("review/run.json", run)
# 现有Figma工具执行实际导出，并重新读取来源签名后：
record_export(run, snapshot, node_id, relative_path, exports_directory,
              actual_export_setting, before_signature, after_signature)
checkpoint("review/run.json", run)
```

来源签名必须是工具实际读取值，不可复制旧值来补齐记录。`record_export` 核对所选导出设置和前后签名，读取实际PNG尺寸/哈希。记录绑定run_id、需求SHA256、范围SHA256、快照SHA256和各文件来源，不用文件名或尺寸相同证明同版。

```sh
python scripts/validate_manifest.py "audit-manifest.json" \
  --snapshot "review/snapshot.json" --exports-dir "review/exports" \
  --evidence "review/run.json" --report "review/report.json"
```

没有绑定记录时原PNG检查仍可运行，但 `exports.binding` 显示“未验证”。文件尺寸和透明边界合格，不代表来源一致；测试通过也不代表真实Figma交付。

每页及每批保存断点。恢复时核对需求、来源快照、路径存在性与实际哈希；已删除或被修改的PNG不能复用。遇到调用限额，`checkpoint(..., stop_reason="Figma调用限额")` 保存停止状态，结束相关请求，不连续重试。停止或已清理的轮次不自动恢复缓存。普通连接错误先恢复只读状态，再决定是否继续。恢复采集来源变化时新建轮次，不覆写旧轮次来源。

### 改稿后增量导出

完整导出前先消除已知文字、单位、指针等问题，不用全量PNG检查代替可以直接完成的节点预检。首次稳定版本导出一次；修改后重新采集正式精简快照，按实际资源依赖判断PNG复用，避免第二次无差别导出全部状态。

```python
from run_evidence import plan_exports, advance_run, cleanup_exports

plan = plan_exports(run, requirements, previous_snapshot, current_snapshot, exports_directory)
# plan['export']：本轮要导出的路径；plan['reuse']：可复用路径。
# plan['affected_ids']：同时沿旧、新依赖图传播，覆盖删除、移出和母件引用。
next_run = advance_run(run, requirements, previous_snapshot, current_snapshot, exports_directory)
checkpoint("review/next-run.json", next_run)  # 旧run及快照保留
# 现有Figma导出工具只处理plan['export']，每批通过record_export登记到next_run。
# 所有当前正式文件齐全后，仍以current_snapshot与next_run运行完整文件校验。
# 本任务结束且检查结果已保存后：
cleanup_exports(next_run, exports_directory)
checkpoint("review/next-run.json", next_run)
```

跨版本复用要求：需求与采集配置不变、前后快照分页完整、资源子树/引用母件/祖先样式及子节点顺序的指纹不变、路径和实际导出设置匹配、PNG仍存在且哈希一致。母件变更沿依赖关系影响Active、AOD及缩略图。新轮次记录 `reused_from` 的原轮次、快照、来源签名、资源指纹和PNG哈希，明确是旧文件复用，不能声称重新导出。校验器重新核对当前资源指纹。

旧记录没有资源指纹、引用无法解析或实例子树被跳过时，不跨版本复用相关资源；不凭母件ID相同猜测实例内部没变。只影响这些资源，无需让其他已证明未变的字模一起失效。同一快照重复调用不会新建重复轮次。配置变化、删除PNG或任务已经清理后按需重新导出，不伪造缓存。

`cleanup_exports` 只删除本轮及已淘汰映射中登记且哈希未变化的PNG，保留参考源图、未登记文件和被修改的文件；返回 `removed/retained`，后者须在最终报告说明。预览临时文件也应登记，或另记明确清理清单，不扫描并删除整目录内所有PNG。文件删除不等同于减少Figma调用额度。

每轮统计工具尝试次数、失败次数和导出批次；一次工具调用可能包含多个节点导出，不能把切图数量当成调用次数，也不能把工具次数直接当作官方额度扣减。没有服务端计量回执时不报告确定的额度消耗或节省比例。

`impact_scope` 按子节点→父组件→引用实例传播，列出受影响的导出及用户维护区域。先检查影响范围，避免共享字模连带修改受保护区域。纯名称迁移不触发整盘PNG导出。

临时PNG检查后仅删除本轮创建的文件，保留必要源图、尺寸、哈希和报告，并把断点文件状态改为 `cleaned`。清理后的证据是历史检查记录，不能宣称当前文件存在。自动检查、用户验收与开发真机验证分开记录。

若主体快照跳过实例子树，可以用同轮Figma只读遍历补充`composition_proof`：`capture_signature`必须与该快照一致，`roots`逐一覆盖主稿、AOD和预览，记录`root_id`、实际遍历数`visited`及每个可见叶子的`id/type/instance_id/master_id`。必须真的展开实例遍历；不以手填零违规数代替叶子记录。此证据只补足实例组装检查，不代替内部样式、几何或真机行为检查。

纯数据指针进度使用`progress_representation: "pointers"`，同时提供实际`pointers`映射；进度条带底检查为不适用，指针轴心/扫过范围仍必须验证。

## 多尺寸任务

多尺寸适配时按[多尺寸适配](multi-size.md)的目标规格、同名资源作用范围、共享采集和批次证据执行。单尺寸480兼容入口保留；不把480尺寸、圆形掩模或256缩略图固定应用到其他目标。

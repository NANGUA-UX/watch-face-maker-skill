# 通用字模母件生成器

先运行[开局预检](project-entry.md)，再由现有 `use_figma` 工具加载 `scripts/text_asset_builder.js`。仅支持单行、单一字体样式的数字、标点、单位、星期、月份与标签；混合字体、弧形文字、带效果的源文字、非居中描边、旋转/缩放布局、缺字体或缺完整测量均停止，不近似替换。

```javascript
// 先嵌入text_asset_builder.js；输入取自已确认project-spec，不猜节点ID。
const input = {
  operation: 'create', // 局部修复为modify
  resource_section_id: '<资源SECTION ID>',
  scope: {node_ids: ['<SECTION ID>'], instance_ids: []},
  families: [{
    id: 'Hour', kind: 'time_hour',
    style: {font_name: {family: 'Inter', style: 'Regular'}, font_size: 32},
    entries: [{characters: '0', path: '<已确认字符路径>', x: 20, y: 20}]
  }]
};
const measurement = await measureTextAssets(input);
const plan = planTextAssets(measurement);
return await applyTextAssets(plan);
```

三个步骤可在同一工具调用中顺序执行；需要独立评审计划时保存完整测量与计划，不截断签名。工具输出有大小限制，按资源族制作或只返回结果ID与摘要，原始节点另作分页回读。计划包含测量，不依赖Figma插件内跨调用状态。

支持kind：`time_hour/time_minute/second/data/date/symbol/unit/week/month/label`。字体样式与 `style_source_node_id`二选一；后者必须读取原生TEXT，不从图片猜字体。字符到资源路径逐项映射；路径必须唯一，不按数组顺序替用户改命名。坐标来自已确认排布，示例像素不是通用规则。

AOD族设置 `aod:true`；AOD词条用 `pair_path`明确指向相同字符的亮屏路径，包含分隔符。先测量无填充、至少2px灰色描边后的完整边界；保留已确认的更粗描边。配对族按联合包络计算相同高度，每对使用相同宽度。数字主时间变宽，秒数/数据/日期同族数字等宽；单位与标点收紧。

临时容器不裁切，真实TEXT的 `absoluteRenderBounds`与字体排版原点一起测量，成功/失败均清理。TEXT居中、宽高正偶数，位置落在0.1px网格；切图宽高为最小安全的4倍数。字模族使用共同字体基线，标点不独自垂直居中。将返回的 `resource_families`并入正式快照，保留 `baseline_policy:shared_font_baseline`，由[现有几何校验](verification.md)检查共同基线、水平中心和完整边界；没有此声明的旧项目保持原居中规则。

新建重复运行发现同名同内容同几何时保留节点；冲突要求明确切换局部修复。修复每个词条登记 `component_id`，scope纳入母件与所有关联实例ID；仍需包含资源SECTION。只支持该分区直属的单TEXT母件。保留ID、内容、字体、字号和母件位置，调整计划中的几何/对齐及AOD描边。原生 `getInstancesAsync()`发现范围外实例、远程母件、自动布局、旋转/缩放或内容覆盖时停止，报告具体影响ID；不替用户移动排布。

应用前重新读取源、实例和分区排布，签名不一致就重新测量；不能回放旧计划。跨族相交或越界停止。返回全部新增、修改、保留、清理ID，以及资源族映射；按[证据契约](evidence-contract.md)回采实际几何并导出1× PNG，检查配对、alpha边界与内容，不能拿计划尺寸当实测结果。

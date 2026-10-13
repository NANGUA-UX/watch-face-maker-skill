# 工作流合同

`workflow-contract.json` 是新任务的阶段状态源。复制[示例](workflow-contract.example.json)后必须替换尺寸、屏形、模式、来源策略和项目内容；示例中的 `1×1` 只是不能误当成正式目标的占位值。

## 字段

- `schema_version`：当前固定为 `1`。
- `status`：`in_progress`、`draft_reviewable`、`production_ready` 或 `needs_stronger_model_or_human_asset`。
- `task_mode`：`replicate`、`adapt` 或 `original`。
- `complexity`：`complex_visual`、`template` 或 `native`。
- `target`：用户确认的 `width`、`height` 和 `shape`；屏形为 `circle` 或 `rounded_rect`（兼容旧合同的 `round_rect`）。
- `source_strategy`：`mode` 沿用清单的 `new`、`modify`、`reuse_allowed`、`reuse_forbidden`；用户明确来源权限后把 `reuse_permission_confirmed` 设为 `true`。
- `reference_contract`：参考图哈希、必须保持的不变量、允许调整项和禁止内容。复刻任务必须有参考图哈希和不变量。
- `production_routes`：每个区域一项，`primary` 与 `fallback` 从 `figma`、`reference_image_edit`、`approved_asset` 中选择且不能相同。使用参考图编辑时在 `reference_input_hashes` 记录实际直传的参考图哈希。
- `gates`：固定包含 `intake`、`reference`、`prototype`、`assembly`、`visual_preflight`，状态为 `pending`、`pass`、`fail` 或 `not_applicable`。只有非复杂视觉任务的样稿阶段可用 `not_applicable`。
- `attempts`：按发生顺序记录 `region`、`route`、`outcome` 和布尔值 `improvement`。`outcome` 为 `pass`、`improved` 或 `failed`。

## 门禁语义

门禁按固定顺序检查，后阶段不能在前阶段未完成时标记通过。需求入口只检查本阶段事实，不提前要求参考阶段产物。复杂视觉任务的代表性样稿不能省略。复刻任务的主路线或降级路线采用 `reference_image_edit` 时，合同中登记的每张参考图都必须出现在该区域的 `reference_input_hashes`。

同一区域主路线连续两次 `improvement: false` 后，该区域的下一次尝试必须立即使用登记的降级路线，不能先继续主路线再补一次降级尝试。任何降级尝试（包括提前降级、已有改善后的后续尝试）为 `failed` 时立即将 `status` 改为 `needs_stronger_model_or_human_asset`；后续成功不能覆盖本应停止的失败。门禁继续返回失败，使任务停在可评审状态。

```bash
python3 "<SKILL_DIR>/scripts/workflow_guards.py" workflow-contract.json --gate prototype
```

这是只读合同的底层检查，不绑定project-spec。已建立项目事实的任务必须使用`watch_face.py check <项目目录> --gate prototype`先核对当前需求，再进入合同门禁。返回码 `0` 为通过、`1` 为门禁失败、`2` 为输入结构无效。

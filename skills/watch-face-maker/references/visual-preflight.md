# 视觉预检

视觉预检把参考图和当前实现变成可追溯证据，不自动判断审美，也不计算相似度。

## 生成证据

为每个会影响参考契约的不变量提供至少一个关键区域。坐标使用目标表盘画布坐标；一个 `--crop` 生成左参考、右实现的局部并排图。

```bash
uv run --with pillow python "<SKILL_DIR>/scripts/build_visual_review.py" \
  reference.png implementation.png \
  --contract workflow-contract.json \
  --snapshot figma-snapshot.json \
  --manifest audit-manifest.json \
  --exports-dir exports \
  --implementation-node-id <presentation-main-node-id> \
  --output-dir review \
  --crop subject:40,60,260,300 \
  --crop main-time:20,120,180,160
```

生成器按合同目标尺寸对两张输入图做居中填充裁切，输出：

- 保留原始字节的 `reference-source.<原格式>`，及 `reference-fitted.png` 与 `implementation-fitted.png`；
- `side-by-side.png`；
- `overlay.png`，两图各占一半权重；
- 每个关键区域的 `crop-<name>.png`；
- 绑定合同、快照、来源和证据文件 SHA-256 的 `visual-review.json`。

传入的实现图必须是 `exports` 中由当前快照 `export_items` 登记为 `presentation_main` 的文件。生成器把节点、相对路径和文件 SHA-256 写入 `implementation_export`；最终校验器会重新读取当前快照和导出文件核对，旧图或任意临时图不能通过。最终校验会从归档参考图和当前导出重新生成对比像素，核对并排图、叠图和局部图；只更新哈希不能沿用旧视觉证据。

生成后所有检查初始为 `unverified`。实际查看图片，再逐项修改 `checks[].status`；每条检查的 `evidence` 必须引用 `side_by_side`、`overlay` 或局部图 ID。不要先把合同视觉门禁改成通过，否则会改变合同哈希。

全部不变量评为 `pass` 后运行封存命令。它复用最终校验器，先确认组装门禁、当前合同与快照哈希、所有检查状态、实际证据文件和派生像素，再保留已有结论，同时更新合同状态与视觉记录中的合同哈希：

```bash
uv run --with pillow python "<SKILL_DIR>/scripts/finalize_visual_review.py" \
  workflow-contract.json review/visual-review.json \
  --manifest audit-manifest.json --snapshot figma-snapshot.json --exports-dir exports
```

## 判断边界

- `pass`：不变量在证据中清楚成立。
- `fail`：构图、主体、材质、信息、来源、重叠或其他不变量明确不成立。
- `unverified`：证据缺失、遮挡、分辨率不足或仍需用户判断。

`fail` 和 `unverified` 都不能取得正式生产通过。自动生成图片、文件哈希和结构检查只能证明证据对应当前版本，不能证明视觉已经通过。

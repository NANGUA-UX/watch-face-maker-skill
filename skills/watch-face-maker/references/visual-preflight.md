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

局部修复以本轮锁定的认可高清源核对保护项，明确检查弧形排版、指针轮廓和材质主体是否保持。整图标族查看当前真实全族接触表与目标尺寸整盘，并在 `design-review.md` 记录系列一致性；不要把预览图回灌为修复源或新增自动审美分数。对照本轮影响清单复核背景、动态前景、AOD、Preview和标注的实际图像/节点，保存受影响部分的新证据，无影响项附依据；只看主稿不能证明修复闭环。

## 背景边缘证据

现有命令不变。快照登记config.background_id及该母件1×PNG导出后，生成器自动输出background_edge_review、完整外圈标记图、八方向放大局部和深浅底11图。最终校验与样稿封存都会从当前输入重新计算事实和派生像素；主展示图视觉pass不能替代这项复核。

图片基底在snapshot-extras中登记以下结构，文件路径相对当前exports目录，不允许越界：

```json
{
  "background_source": {
    "source_node_id": "背景母件直属图片节点ID",
    "source_path": "Source/artwork.png",
    "coverage_basis": "mask",
    "valid_region_path": "Source/valid-region.png"
  }
}
```

- coverage_basis为rectangle时，须实际核对源是连续矩形画面；alpha用于真实透明源，不能用于全不透明RGB黑底；mask用于经核对的有效区域灰度图，尺寸与源图一致，白有效、黑无效。
- source_path保存Figma实际getBytesAsync字节。当前imageHash按导入字节SHA-1核对，另记录SHA-256；源尺寸与Figma images回读一致。字节无法对应时重新保存现场源，不把另一张同尺寸图片冒充实际素材。
- 图片基底须为背景母件直属RECTANGLE、无额外圆角及兄弟遮罩的无旋转FILL或FIT；覆盖同时与图片节点的实际矩形相交。源到背景的像素变换从真实节点矩阵、源尺寸及填充方式计算并写入证据。可选source_to_background用于核对已有登记，不能覆盖实际回读。非矩形、CROP、旋转、额外遮罩等先在Figma合成统一基底，未经验证不近似换算。
- 纯原生背景登记background_source为`{"kind":"native"}`，必须完整采集背景子树及实例内部，确认没有图片填充；不能给位图贴原生标签。
- 局部补边同时提供original_background_path与protected_region_path，均为目标原生尺寸。保护掩膜白色区保留主体，不能为空；实际PNG在保护区各通道差不超过1/255，超出即失败。若源需要重新配准，以已确认锚点为依据，不通过放大掩盖缺口。

自动检查容器、有效画面覆盖及内部透明缺口，最外1px覆盖容差和2px输出alpha带只用于正常抗锯齿，仍须在11图中审看完整外圈；不采用数据安全边距。黑白色本身不判失败：不透明圆外填充由经核对的有效区域和实际边缘图识别，合法黑色、留白和设计边框按参考判断。

实际查看全部边缘证据后，填写background_edge_review.status为pass/fail/unverified及非空notes，其他测量字段和artifacts不手改。来源SHA-256须在清单reference/adopted/reused中登记。容器、有效区域或透明检查失败，边缘证据缺失/过期，或人工结论未验证，都不能取得新通过。旧报告保留原字节；缺少该字段只表示尚未验证，不能补写pass迁移。

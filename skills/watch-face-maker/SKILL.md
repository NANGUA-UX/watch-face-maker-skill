---
name: watch-face-maker
description: Use when 用户需要在 Figma 中新建、复刻、适配或整理智能手表表盘，并交付主表盘、AOD、动态切图与验证证据。
---

# 表盘制作助手

以用户需求和当前 `workflow-contract.json` 为边界。每次只执行一个阶段；上一阶段没有证据通过时，不进入下一阶段。自动校验约束结构与证据，视觉判断、用户验收和真机验证分别记录。

先按[统一项目入口](references/project-entry.md)区分`create/modify/organize/audit`，与设计模式分开。已确认需求维护在`project-spec.json`；只问缺失或冲突项。新建走完整状态机，修改/整理从受影响阶段接续，审核只读。预检分开报告本地依赖和Figma现场证据；未确认尺寸或不支持规格不能默认改为480×480，不通过试写探测权限。尚未建立的Active/AOD/缩略图不是新建前置条件。

## 七阶段状态机

先读[阶段卡片](references/stage-cards.md)，按其中固定的“输入、操作、产物、通过条件、失败处理”逐阶段执行。

1. **需求入口**：确认目标尺寸、屏形、语言、任务模式和素材复用权限。未确认尺寸或复用权限时只读检查，不写入 Figma。
2. **参考契约**：记录参考图哈希、不变量、允许调整项和禁止内容。复刻任务使用生成工具时，实际参考图必须直接作为输入。
3. **制作路线**：每个区域登记 `figma`、`reference_image_edit`、`approved_asset` 中唯一的主路线和不同的降级路线。进度表现根据结构与开发能力选择 `state_images` 或 `native_continuous`。
4. **代表性样稿**：复杂插画、材质或 AI 背景先完成主体、主时间和一个关键功能区。按阶段卡片生成并评审实际图片，再用 `finalize_visual_review.py --stage prototype` 封存证据；手写 `pass` 不能通过门禁。样稿未通过，不制作完整状态、AOD或多尺寸版本；模板和完整原生方案可记 `not_applicable`。
5. **完整组装**：建立切图母件、Active、AOD、缩略图和状态资源，并检查跨族碰撞、越界、引用、导出和来源。
6. **视觉预检**：生成同尺寸并排图、半透明叠图和关键局部图；按参考契约逐项填写 `pass/fail/unverified`。不使用自拟相似分数。
7. **正式验证**：绑定当前合同、快照、PNG、素材来源和哈希，分别报告草稿评审、生产校验、用户验收和固件验证。

从[合同示例](references/workflow-contract.example.json)复制项目合同后替换全部项目事实；示例中的待确认值不能直接通过门禁。进入阶段前运行：

```bash
python3 "<SKILL_DIR>/scripts/watch_face.py" check <项目目录> --gate <intake|reference|prototype|assembly|visual_preflight>
```

返回码 `0` 才能进入下一阶段；`1` 表示门禁失败，`2` 表示输入结构无效。入口核对当前需求确认及合同派生字段，再调用合同门禁。完整字段与失败规则见[工作流合同](references/workflow-contract.md)。

## 跨阶段硬规则

- 分清用户请求和附件说明。疑问、反问和效果评价先作为反馈处理，不能自行扩写成新构图要求。已有明确决定不重复询问；新的截图会使受影响区域的旧视觉结论失效。
- 用户要求全新制作或禁止复用时，旧稿只读；创建新节点不等于新素材。参考图和采用素材记录 SHA-256。单盘坐标、颜色、节点 ID 和偶发现象不得写成通用规则。
- 复刻先锁定主体、姿势、比例、位置、视角和场景关系。视觉证据必须检查轮廓、材质、光影、透明边缘、信息含义和实际尺寸画面；结构或哈希通过不能代替视觉结论。
- 背景按[满幅流程](references/background-composition.md#背景满幅制作与边缘证据)制作连续画面，由Figma统一屏形裁切；默认保持构图、局部补边。样稿和正式验证都须提供background_edge_review及当前1×PNG边缘证据，不能用FILL或框尺寸代替铺满检查。
- 同一区域同一路线连续两次没有明确改善，必须切换合同登记的降级路线；降级仍失败时把合同 `status` 改为 `needs_stronger_model_or_human_asset`，停在可评审状态，不继续自由生成或宣称完成。
- 切图组件是母件；主表盘、AOD和缩略图由实例组装。每个亮屏布局只维护一个 Active 来源。资源区使用 `SECTION`，检查直属可见节点跨族相交和越界。
- 动态数据先列状态矩阵。进度模式由形状、材质、连续性和开发能力决定；不能因业务名强制使用状态图。功能图标先查指定组件库，缺失时用纯色色块占位并记录检索范围。
- 规则进度、底轨和装饰弧/直线先读[描边与端点](references/stroke-geometry.md)：使用无填充的原生描边，端点按参考设置，不默认叠加圆形端帽；登记并采集真实路径证据，结构与视觉分别验证。
- 制作规则圆形刻度前必读[圆形刻度](references/circular-ticks.md)：默认采用“同心径向线阵列 → Union → 与圆环 Intersect”，保留可编辑源；长短刻度分组。不得默认以逐个定位的小矩形替代；例外须有用户指令或具体形状依据。检查源结构、共圆心、角度和实际可见数量，不只检查外观。
- 所有文字切图内部 `TEXT` 水平居中，包括数字、单位、符号、星期、月份和标签。切图宽高为 4 的倍数；文本框宽高为正偶数整数；完整字形和基线不得切边。详细几何与命名按[交付验证](references/verification.md)和[命名与状态](references/naming-and-states.md)执行。
- 单行单样式文字使用[字模母件生成器](references/text-asset-builder.md)的测量→计划→应用流程。修复限定母件与所有关联实例范围，保留ID、内容、字体、字号及用户排布；源变化重新测量，不支持项停止。新生成族显式登记共同字体基线，旧族不自动改变排版政策。
- 主时间（Hour、Minute 及 AOD 配对）按实际字符使用变宽切图；这个例外不扩展到秒数。秒数与普通数据、日期的同族 `0–9` 默认统一宽高，符号和单位仍按排版语义处理。不得把某个表盘的具体像素尺寸写成通用值。
- AOD 默认制作。数字时间和 AOD 指针在目标原生尺寸下使用无填充灰色描边，默认及最小宽度均为 2 px；缩放后复查母件和实例。亮屏与 AOD 的每个时间字符逐对同宽同高，包括分隔符。
- 表盘英文名可以有空格；切图前缀去掉空格后连写，不使用下划线。默认仅制作英文；变色、多语言、特殊日期、动画和开发文档列出的其他能力均按需启用。
- 指针盘读[指针规则](references/analog-pointers.md)，多尺寸任务读[多尺寸适配](references/multi-size.md)，动画任务读[动画切图](references/animation-assets.md)。背景边界、AI素材和选定版本收尾读[混合制作与素材质量](references/background-composition.md)。

## 视觉预检与正式命令

先按[视觉预检](references/visual-preflight.md)生成证据；生成器只建立图片与 `unverified` 检查项，不替代人工判断：

```bash
uv run --with pillow python "<SKILL_DIR>/scripts/build_visual_review.py" \
  <reference.png> <implementation.png> \
  --contract <workflow-contract.json> --snapshot <figma-snapshot.json> \
  --manifest <audit-manifest.json> --exports-dir <exports> \
  --implementation-node-id <presentation-main-node-id> --output-dir <review-dir> \
  --crop <name:x,y,width,height>
```

查看证据并把所有不变量逐项评为 `pass` 后，封存视觉记录并同步推进合同门禁；此步骤保留人工结论并更新当前合同哈希：

```bash
uv run --with pillow python "<SKILL_DIR>/scripts/finalize_visual_review.py" \
  <workflow-contract.json> <visual-review.json> \
  --manifest <audit-manifest.json> --snapshot <figma-snapshot.json> --exports-dir <exports>
```

仅检查旧清单时仍可运行：

```bash
python3 "<SKILL_DIR>/scripts/validate_manifest.py" <audit-manifest.json>
```

旧项目缺少合同保持兼容，但只能显示“未验证”。新任务正式验证必须提供全部绑定证据：

```bash
uv run --with pillow python "<SKILL_DIR>/scripts/validate_manifest.py" <audit-manifest.json> \
  --snapshot <figma-snapshot.json> --exports-dir <exports> --evidence <run-evidence.json> \
  --workflow-contract <workflow-contract.json> --visual-review <visual-review.json> \
  --report <report.json>
```

快照、分页合并、增量导出、断点复用和清理按[实际证据契约](references/evidence-contract.md)；完整制作细节按[完整工作流](references/workflow.md)，参考合理性按[设计合理性评审](references/design-rationality.md)。

## 停止条件

- 精确目标、尺寸、屏形、来源权限或用户修改授权不能确认：停止写入。技术写权限未验证时，按项目入口规则执行首个已授权的必要制作动作并保存真实回执；明确拒绝或失效回执仍停止，不试写探测。
- Figma 写入报错：读取错误并确认原子回滚状态，不连续盲重试。
- 门禁、视觉、结构、来源、导出或清单任一失败：不得进入下一阶段或宣称交付完成。
- 证据缺失：记录 `unverified`；不得用空违规列表、旧报告、局部 PNG 或自动测试通过代替当前验证。

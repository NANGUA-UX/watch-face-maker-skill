# 规则线条：真实描边与原生端点

适用于规则进度前景、固定底轨、装饰弧线和直线。图标、插画、异形分段和复杂材质沿用各自路线；不把所有椭圆判成端帽。

| 形态 | 原生构造 | 样式 |
| --- | --- | --- |
| 规则圆弧 | 优先原生 `ELLIPSE` 圆弧，`innerRadius=1`，用`arcData`控制起止角 | `fills=[]`，真实 `strokes`、正 `strokeWeight` |
| 直线 | `LINE` | 同上 |
| 完整圆环 | 完整 `ELLIPSE`，`innerRadius=0`或保留原生圆弧的`1` | 同上；闭环没有端点 |

规则圆弧优先使用可直接拖动起止角的原生椭圆弧，保持完整圆的宽高与圆心，不以当前短弧的紧边框替代整个圆。只有非圆路径或原生椭圆无法表达的明确造型才使用开放VECTOR；既有合法VECTOR保持兼容，不因本规则重做已验收稿，也不为通过校验将原生圆弧转曲。

线宽、描边对齐和端点来自参考或已确认需求，不能统一改成圆头。圆头用 `ROUND`，平头用 `NONE`，方头用 `SQUARE`；VECTOR 顶点可覆盖节点端点属性，必须回读实际端点。渐变与光晕直接放在同一描边对象上，默认不另画圆形端帽。旧案例的“环形扇区改填充”仅是当时扇区问题的修法，不是规则进度的通用构造。

## 最小 Plugin API 示例

示例坐标仅演示 API，实际尺寸、位置、线宽、颜色和端点由项目需求确定。执行前确认写入范围；每条线使用一个无填充、无裁切的专用 FRAME/COMPONENT 逻辑根。标题、对照源和辅助线放在根外。

```js
// 四个相互独立的演示逻辑根；调用方已确认当前页面可写。
function demoRoot(name,x) {
  const n=figma.createFrame(); n.name=name; n.resize(160,160);
  n.x=x; n.fills=[]; n.strokes=[]; n.clipsContent=false;
  return n;
}
const ordinaryRoot=demoRoot('StrokeDemo/Arc',0);
const gradientRoot=demoRoot('StrokeDemo/Gradient',200);
const lineRoot=demoRoot('StrokeDemo/Line',400);
const circleRoot=demoRoot('StrokeDemo/Circle',600);
const solid = {type:'SOLID', color:{r:.6,g:.3,b:1}};
const gradient = {
  type:'GRADIENT_LINEAR', gradientTransform:[[1,0,0],[0,1,0]],
  gradientStops:[
    {position:0,color:{r:.6,g:.3,b:1,a:1}},
    {position:1,color:{r:.2,g:1,b:1,a:1}}
  ]
};
function stroke(node, paint, cap) {
  node.fills=[]; node.strokes=[paint]; node.strokeWeight=8;
  node.strokeAlign='CENTER'; node.strokeCap=cap;
  return node;
}
// 原生椭圆圆弧：保留编辑器的起止角控制柄。
function arc(root, paint=solid, cap='ROUND') {
  const n=figma.createEllipse(); root.appendChild(n); n.resize(100,100);
  n.arcData={startingAngle:-Math.PI/2,endingAngle:0,innerRadius:1};
  n.x=16; n.y=16; return stroke(n,paint,cap);
}
const ordinary=arc(ordinaryRoot,solid,'NONE');
const luminous=arc(gradientRoot,gradient,'ROUND');
luminous.effects=[{type:'DROP_SHADOW',color:{r:.2,g:1,b:1,a:.35},
  offset:{x:0,y:0},radius:6,spread:0,visible:true,blendMode:'NORMAL'}];
const line=figma.createLine(); lineRoot.appendChild(line);
line.resize(100,0); line.x=16; line.y=80; stroke(line,solid,'SQUARE');
const circle=figma.createEllipse(); circleRoot.appendChild(circle);
circle.resize(100,100); circle.x=16; circle.y=16;
circle.arcData={startingAngle:0,endingAngle:2*Math.PI,innerRadius:0};
stroke(circle,gradient,'NONE');
```

动态示意按已确认的起点、扫角和渐变坐标更新**同一个原生椭圆弧的arcData**；不随意改变主体位置。以完整一圈为量程，三态独立评审样例为：

| 数值 | 结构与画面 |
| --- | --- |
| 0% | 保留合法原生椭圆弧模板但 `visible=false`；不使用零长度圆头，不留圆点 |
| 50% | 同一ELLIPSE的半圆范围，`innerRadius=1`，起止角由量程推导；描边端点按确认值 |
| 100% | 同一ELLIPSE更新为完整一圈，可保留`innerRadius=1`；旧整圆`innerRadius=0`兼容，不额外叠加端帽 |

若量程本身是开口弧，100% 仍是该原生椭圆弧，不改成整圆。原生ELLIPSE从部分圆弧到整圈只更新arcData并同步登记形态，保留节点与母件ID；无须改成VECTOR或换路径。旧VECTOR确需切换为原生整圆时才替换子路径、更新path_id并重新采集，不能残留隐藏重复路径。此为设计示意，固件映射另行验证。

## 采集与验证

1. 与设计评审逐项核对所有适用线条，登记 `stroke_geometry`。每个逻辑根是独立 FRAME/GROUP/COMPONENT，只容纳该路径及无绘制容器；不得用路径自身作为根缩小检查范围。完整资源分区仍单独检查，不要将整个背景/表盘作为单条线逻辑根。
2. `config.stroke_geometry_node_ids` 填写全部登记的 `path_id`，使用 `validation` 或 `full` 采集；`validation` 只为这些路径保留原始 `vectorNetwork/vectorPaths`。仍保留全部根后代，不能用跳过节点掩盖端帽。
3. `snapshot.stroke_geometry` 按类型核对原生椭圆弧参数或开放VECTOR拓扑，并核对无可见填充、有效描边、线宽、节点/顶点端点、专用根的额外几何；闭合整圆不要求端点。结构不足为 `unverified`，错误为 `fail`。检查不证明圆弧的视觉质量、精确曲率或固件映射。
4. 视觉检查 0/50/100%、三种端点、渐变衔接、光晕及实际裁切；路径/填充/线宽/端点变化必须使旧签名、资源指纹和对应视觉结论失效，重新取得同轮证据。

未提供新清单的旧快照为未验证，旧报告不改字节。确无适用线条时明确 `stroke_geometry: []`，仍需评审核对没有漏登记；存在 `native_progress` 时不得声明为空。自动检查不负责发现所有漏登记对象，也不能替代视觉或跨模型验收。

接口与正式命令见[证据契约](evidence-contract.md)。官方接口：[strokeCap](https://developers.figma.com/docs/plugins/api/properties/nodes-strokecap/)、[VectorVertex](https://developers.figma.com/docs/plugins/api/VectorVertex/)。

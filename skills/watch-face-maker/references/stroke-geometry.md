# 规则线条：真实描边与原生端点

适用于规则进度前景、固定底轨、装饰弧线和直线。图标、插画、异形分段和复杂材质沿用各自路线；不把所有椭圆判成端帽。

| 形态 | 原生构造 | 样式 |
| --- | --- | --- |
| 规则圆弧 | 开放 `VECTOR` 路径 | `fills=[]`，真实 `strokes`、正 `strokeWeight` |
| 直线 | `LINE` | 同上 |
| 完整圆环 | 完整 `ELLIPSE`，`innerRadius=0` | 同上；闭环没有端点 |

线宽和端点来自参考或已确认需求，不能统一改成圆头。圆头用 `ROUND`，平头用 `NONE`，方头用 `SQUARE`；VECTOR 顶点可覆盖节点端点属性，必须回读实际端点。渐变与光晕直接放在同一描边对象上，默认不另画圆形端帽。旧案例的“环形扇区改填充”仅是当时扇区问题的修法，不是规则进度的通用构造。

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
// 开放三次贝塞尔四分之一圆，无 Z 闭合命令。
function arc(root, paint=solid, cap='ROUND') {
  const n=figma.createVector(); root.appendChild(n);
  n.vectorPaths=[{windingRule:'NONZERO',data:'M 100 0 C 100 55.228 55.228 100 0 100'}];
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

动态示意按已确认的起点、扫角和渐变坐标更新**同一条路径**；不随意改变主体位置。以完整一圈为量程，三态独立评审样例为：

| 数值 | 结构与画面 |
| --- | --- |
| 0% | 保留合法开放路径模板但 `visible=false`；不使用零长度圆头，不留圆点 |
| 50% | 单条开放半圆 VECTOR；如 `M 0 50 C 0 22.386 22.386 0 50 0 C 77.614 0 100 22.386 100 50`；描边端点按确认值 |
| 100% | 完整 ELLIPSE 描边闭环；不额外叠加起止端帽 |

若量程本身是开口弧，100% 仍是该开放弧，不改成整圆。若切换闭环需替换路径类型，保留逻辑根、移除旧子路径并更新 `path_id` 后重新采集；不得在根内残留隐藏重复路径。此为设计示意，固件映射另行验证。

## 采集与验证

1. 与设计评审逐项核对所有适用线条，登记 `stroke_geometry`。每个逻辑根是独立 FRAME/GROUP/COMPONENT，只容纳该路径及无绘制容器；不得用路径自身作为根缩小检查范围。完整资源分区仍单独检查，不要将整个背景/表盘作为单条线逻辑根。
2. `config.stroke_geometry_node_ids` 填写全部登记的 `path_id`，使用 `validation` 或 `full` 采集；`validation` 只为这些路径保留原始 `vectorNetwork/vectorPaths`。仍保留全部根后代，不能用跳过节点掩盖端帽。
3. `snapshot.stroke_geometry` 核对真实开放拓扑、无可见填充、有效描边、线宽、节点/顶点端点、专用根的额外几何；闭合整圆不要求端点。结构不足为 `unverified`，错误为 `fail`。检查不证明圆弧的视觉质量、精确曲率或固件映射。
4. 视觉检查 0/50/100%、三种端点、渐变衔接、光晕及实际裁切；路径/填充/线宽/端点变化必须使旧签名、资源指纹和对应视觉结论失效，重新取得同轮证据。

未提供新清单的旧快照为未验证，旧报告不改字节。确无适用线条时明确 `stroke_geometry: []`，仍需评审核对没有漏登记；存在 `native_progress` 时不得声明为空。自动检查不负责发现所有漏登记对象，也不能替代视觉或跨模型验收。

接口与正式命令见[证据契约](evidence-contract.md)。官方接口：[strokeCap](https://developers.figma.com/docs/plugins/api/properties/nodes-strokecap/)、[VectorVertex](https://developers.figma.com/docs/plugins/api/VectorVertex/)。

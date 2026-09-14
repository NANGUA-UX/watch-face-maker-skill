/** 在 use_figma 制作脚本前加载；只计算几何，不改节点、不推断图标语义。 */
function iconTransform(sourceTransform, width, height) {
  const lens=[0,1].map(c=>Math.hypot(sourceTransform[0][c],sourceTransform[1][c]));
  if (!lens.every(v=>Number.isFinite(v)&&v>0)) throw Error('库源变换无效');
  const t=[0,1].map(r=>[sourceTransform[r][0]/lens[0],sourceTransform[r][1]/lens[1],0]);
  const corners=[[0,0],[width,0],[0,height],[width,height]];
  for(let r=0;r<2;r++)t[r][2]=-Math.min(...corners.map(([x,y])=>t[r][0]*x+t[r][1]*y));
  return t;
}
function stateSlot(index, layout) {
  if(!Number.isInteger(index)||index<0||!Number.isInteger(layout.columns)||layout.columns<1)throw Error('状态槽位无效');
  return [layout.origin[0]+index%layout.columns*layout.step[0],layout.origin[1]+Math.floor(index/layout.columns)*layout.step[1]];
}
function annotationGeometry(pivot, center, offset=[0,0]) {
  const axis=center.map((v,i)=>v+offset[i]);
  return {origin:axis.map((v,i)=>v-pivot[i]),axis,lineTop:axis[1]-pivot[1],lineHeight:pivot[1]};
}
if(typeof module!=='undefined')module.exports={iconTransform,stateSlot,annotationGeometry};

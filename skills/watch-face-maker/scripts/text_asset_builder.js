/** 嵌入use_figma：measureTextAssets → planTextAssets → applyTextAssets。
 * 测量产生临时节点并清理；计划只读；应用前复查源及全部关联实例。
 */
const textAssetKinds = new Set(['time_hour','time_minute','second','data','date','symbol','unit','week','month','label']);
const assetGray = [{type:'SOLID',color:{r:179/255,g:179/255,b:179/255}}];
const cloneAssetValue = value => JSON.parse(JSON.stringify(value));
const assetRound = (v, step) => Math.max(step, Math.ceil((v-1e-7)/step)*step);
const assetGrid = (v, min, max) => {
  const low=Math.ceil((min-1e-7)*10)/10,high=Math.floor((max+1e-7)*10)/10;
  if(low>high) throw Error('完整字形无法在0.1px坐标网格内安全定位');
  return Math.max(low,Math.min(high,Math.round(v*10)/10));
};
const assetMinimumWidth = r => {
  const bearing=r.bounds.x+(assetRound(r.advance_width,2)-r.advance_width)/2;
  return assetRound(r.bounds.width+bearing+Math.ceil(-bearing*10-1e-7)/10,4);
};
const assetFontAvailable = (fonts,name) => fonts.some(f=>f.fontName.family===name.family && f.fontName.style===name.style);
function assetMeasureBounds(text) {
  const b=text.absoluteRenderBounds,origin=text.absoluteTransform;
  if(!b || ![b.x,b.y,b.width,b.height].every(Number.isFinite) || b.width<=0 || b.height<=0)
    throw Error('无法取得完整字形测量');
  return {x:b.x-origin[0][2],y:b.y-origin[1][2],width:b.width,height:b.height};
}
function prepareAssetMeasurementText(text) {
  const width=assetRound(text.width,2),height=assetRound(text.height,2);
  text.textAutoResize='NONE';text.resize(width,height);
  text.textAlignHorizontal='CENTER';text.textAlignVertical='TOP';
  return {advance_width:width,line_height:height,bounds:assetMeasureBounds(text)};
}
const assetCanonical = value => JSON.stringify(value, (_,v) => {
  if(v && typeof v==='object' && !Array.isArray(v)) return Object.fromEntries(Object.keys(v).sort().map(k=>[k,v[k]]));
  return v;
});

function validateTextInput(input) {
  if(!input || !['create','modify'].includes(input.operation) || !input.resource_section_id ||
     !input.scope || !Array.isArray(input.scope.node_ids) || !Array.isArray(input.scope.instance_ids) ||
     !Array.isArray(input.families) || !input.families.length) throw Error('缺少操作、资源分区、明确范围或字模族');
  const paths=new Set(),ids=new Set();
  for(const family of input.families) {
    if(!family.id || ids.has(family.id) || !textAssetKinds.has(family.kind) || !Array.isArray(family.entries) || !family.entries.length)
      throw Error('字模族类型、id或词条无效');
    ids.add(family.id);
    if(!!family.style === !!family.style_source_node_id) throw Error('必须提供唯一的已确认字体样式或原生TEXT来源');
    for(const entry of family.entries) {
      if(typeof entry.characters!=='string' || !entry.characters.trim() || /[\r\n]/.test(entry.characters) ||
         typeof entry.path!=='string' || !entry.path || paths.has(entry.path)) throw Error('只支持非空单行内容及唯一资源路径');
      paths.add(entry.path);
      if(input.operation==='create' && ![entry.x,entry.y].every(Number.isFinite)) throw Error('新建必须登记排布坐标');
      if(input.operation==='modify' && !entry.component_id) throw Error('修复须逐项指定母件ID');
    }
  }
}

function textAssetNodeState(node) {
  if(!node) return null;
  const fields=['x','y','width','height','relativeTransform','characters','fontName','fontSize','textAutoResize',
    'lineHeight','letterSpacing','textCase','textDecoration','textAlignHorizontal','textAlignVertical','fills','strokes',
    'strokeWeight','strokeAlign','effects','layoutMode','clipsContent','visible','locked','overrides','constraints'];
  const state={id:node.id,name:node.name,type:node.type,parent_id:node.parent?.id};
  for(const field of fields) if(field in node) state[field]=node[field];
  if(node.type==='TEXT') state.segments=node.getStyledTextSegments(['fontName','fontSize','lineHeight','letterSpacing','fills']);
  if('children' in node) state.children=node.children.map(n=>textAssetNodeState(n));
  return state;
}

function checkAssetLayout(node) {
  for(let n=node;n && !['PAGE','DOCUMENT'].includes(n.type);n=n.parent) {
    if(n.locked || (n.layoutMode && n.layoutMode!=='NONE') || n.type==='INSTANCE') throw Error('无法安全补偿布局：'+n.id);
    const t=n.relativeTransform;
    if(t && (Math.abs(t[0][0]-1)>1e-6 || Math.abs(t[1][1]-1)>1e-6 || Math.abs(t[0][1])>1e-6 || Math.abs(t[1][0])>1e-6))
      throw Error('不支持旋转或缩放的字模布局：'+n.id);
    if(n!==node && n.clipsContent) throw Error('祖先可能裁切资源：'+n.id);
  }
}

async function checkRepairScope(component, scope) {
  if(!component || component.type!=='COMPONENT' || component.remote || !scope.node_ids.includes(component.id))
    throw Error('母件不在声明范围或远程只读');
  checkAssetLayout(component);
  const instances=await component.getInstancesAsync();
  for(const instance of instances) {
    if(!scope.instance_ids.includes(instance.id)) throw Error('范围外关联实例：'+instance.id);
    // 实例自身type为INSTANCE，检查父布局；尺寸覆盖无法可靠补偿。
    checkAssetLayout(instance.parent);
    const t=instance.relativeTransform;
    if((t && (t[0][0]!==1 || t[1][1]!==1 || t[0][1]!==0 || t[1][0]!==0)) ||
       instance.width!==component.width || instance.height!==component.height ||
       instance.overrides?.some(o=>o.id!==instance.id || o.overriddenFields.some(f=>!['width','height'].includes(f))))
      throw Error('关联实例有缩放、尺寸或内容覆盖：'+instance.id);
  }
  return instances;
}

async function inspectTextInput(input, api) {
  validateTextInput(input);
  const section=await api.getNodeByIdAsync(input.resource_section_id);
  if(!section || section.type!=='SECTION' || !input.scope.node_ids.includes(section.id)) throw Error('资源分区未纳入范围');
  checkAssetLayout(section);
  const records=[];
  for(const family of input.families) {
    const source=family.style_source_node_id ? await api.getNodeByIdAsync(family.style_source_node_id) : null;
    if(family.style_source_node_id && source?.type!=='TEXT') throw Error('样式来源必须为原生TEXT');
    for(const entry of family.entries) {
      let component=null, text=null, instances=[];
      if(input.operation==='modify') {
        component=await api.getNodeByIdAsync(entry.component_id);
        if(component?.parent?.id!==section.id || component.children?.length!==1 || component.children[0].type!=='TEXT')
          throw Error('仅支持资源分区直属的单TEXT母件');
        text=component.children[0];
        if(text.characters!==entry.characters || component.name!==entry.path) throw Error('修复不得改内容或命名');
        checkAssetLayout(text);
        instances=await checkRepairScope(component,input.scope);
      } else {
        const found=section.children.filter(n=>n.name===entry.path);
        if(found.length>1 || (found[0] && (found[0].type!=='COMPONENT' || found[0].children.length!==1 || found[0].children[0].type!=='TEXT')))
          throw Error('已有路径冲突：'+entry.path);
        component=found[0] || null;text=component?.children[0] || null;
        if(text && text.characters!==entry.characters) throw Error('已有母件内容冲突；使用明确范围修复');
      }
      records.push({family,entry,component,text,source,instances});
    }
  }
  // 分区所有直属几何也参与绑定，排布变化时不能回放计划。
  const signature=assetCanonical({input,section:textAssetNodeState(section),
    sources:records.map(r=>({source:textAssetNodeState(r.source),component:textAssetNodeState(r.component),
                            instances:r.instances.map(i=>textAssetNodeState(i))}))});
  return {section,records,signature};
}

function styleOfText(text) {
  const segments=text.getStyledTextSegments(['fontName','fontSize','lineHeight','letterSpacing','fills']);
  if(segments.length!==1 || typeof text.fontName!=='object' || !Number.isFinite(text.fontSize) || text.hasMissingFont ||
     text.effects.length || (text.strokes.length && text.strokeAlign!=='CENTER'))
    throw Error('混合字体样式或缺字体不支持：'+text.id);
  return {font_name:cloneAssetValue(text.fontName),font_size:text.fontSize,
    line_height:cloneAssetValue(text.lineHeight),letter_spacing:cloneAssetValue(text.letterSpacing),
    fills:cloneAssetValue(text.fills),strokes:cloneAssetValue(text.strokes),stroke_weight:text.strokeWeight,
    text_case:text.textCase,text_decoration:text.textDecoration};
}

function setAssetTextStyle(text, style, aod) {
  text.fontName=style.font_name;text.fontSize=style.font_size;
  text.lineHeight=style.line_height || {unit:'AUTO'};
  text.letterSpacing=style.letter_spacing || {unit:'PIXELS',value:0};
  text.textCase=style.text_case || 'ORIGINAL';text.textDecoration=style.text_decoration || 'NONE';
  text.fills=aod ? [] : cloneAssetValue(style.fills || [{type:'SOLID',color:{r:1,g:1,b:1}}]);
  text.strokes=aod ? cloneAssetValue(assetGray) : cloneAssetValue(style.strokes || []);
  text.strokeWeight=aod ? Math.max(2,style.stroke_weight || 0) : (style.stroke_weight || 0);
  text.strokeAlign='CENTER';text.effects=[];
}

function checkRequestedTextStyle(record, expected) {
  if(!record.text) return;
  const actual=styleOfText(record.text),font=expected.font_name;
  if(!font || actual.font_name.family!==font.family || actual.font_name.style!==font.style ||
     actual.font_size!==expected.font_size ||
     (font.variationSettings && assetCanonical(font.variationSettings)!==assetCanonical(actual.font_name.variationSettings)))
    throw Error('已有母件字体或字号与要求冲突：'+record.entry.path);
  for(const key of ['line_height','letter_spacing','text_case','text_decoration'])
    if(expected[key]!==undefined && assetCanonical(expected[key])!==assetCanonical(actual[key]))
      throw Error('已有母件字体样式与要求冲突：'+record.entry.path);
  const equal=(a,b)=>{
    if(typeof a==='number' && typeof b==='number')return Math.abs(a-b)<1e-6;
    if(a && b && typeof a==='object' && typeof b==='object') {
      const keys=Object.keys(a);return keys.length===Object.keys(b).length && keys.every(k=>equal(a[k],b[k]));
    }
    return a===b;
  };
  const paints=p=>p.map(v=>({visible:true,opacity:1,blendMode:'NORMAL',boundVariables:{},...v}));
  if(record.family.aod) {
    if(actual.fills.length || !equal(paints(actual.strokes),paints(assetGray)) || actual.stroke_weight<Math.max(2,expected.stroke_weight || 0))
      throw Error('已有AOD样式与要求冲突：'+record.entry.path);
  } else {
    for(const key of ['fills','strokes'])if(expected[key]!==undefined && !equal(paints(expected[key]),paints(actual[key])))
      throw Error('已有母件颜色样式与要求冲突：'+record.entry.path);
    if(actual.strokes.length && expected.stroke_weight!==undefined && actual.stroke_weight!==expected.stroke_weight)
      throw Error('已有母件描边样式与要求冲突：'+record.entry.path);
  }
}

async function measureTextAssets(input, api=figma) {
  const inspected=await inspectTextInput(input,api), records=[],created=[],cleaned=[],temporary=[];
  let container,failure;
  try {
    container=api.createFrame();created.push(container.id);temporary.push(container);
    container.name='字模临时测量';container.clipsContent=false;container.fills=[];
    container.x=Math.max(200,...api.currentPage.children.filter(n=>n!==container).map(n=>n.x+n.width))+64;
    container.y=200;
    const fonts=await api.listAvailableFontsAsync();
    const familyStyles=new Map();
    for(const r of inspected.records) {
      const expected=r.source ? styleOfText(r.source) : cloneAssetValue(r.family.style);
      checkRequestedTextStyle(r,expected);
      const style=r.text ? styleOfText(r.text) : (r.source ? styleOfText(r.source) : cloneAssetValue(r.family.style));
      if(!style?.font_name || !Number.isFinite(style.font_size) || style.font_size<=0 ||
         !assetFontAvailable(fonts,style.font_name)) throw Error('指定字体不可用；禁止替换');
      await api.loadFontAsync(style.font_name);
      const text=api.createText();created.push(text.id);temporary.push(text);
      // createText默认字体也必须加载后才能改变并归入容器。
      await api.loadFontAsync(text.fontName);
      setAssetTextStyle(text,style,r.family.aod===true);
      style.font_name=cloneAssetValue(text.fontName);style.line_height=cloneAssetValue(text.lineHeight);style.letter_spacing=cloneAssetValue(text.letterSpacing);
      const shared=assetCanonical([style.font_name,style.font_size,style.line_height,style.letter_spacing]);
      if(familyStyles.has(r.family.id) && familyStyles.get(r.family.id)!==shared) throw Error('同族字体排版不一致；不支持近似测量');
      familyStyles.set(r.family.id,shared);
      text.characters=r.entry.characters;text.textAutoResize='WIDTH_AND_HEIGHT';text.textAlignHorizontal='LEFT';
      container.appendChild(text);text.x=0;text.y=0;
      const {advance_width:advance,line_height:lineHeight,bounds}=prepareAssetMeasurementText(text);
      records.push({family_id:r.family.id,kind:r.family.kind,entry:cloneAssetValue(r.entry),style,
        aod:r.family.aod===true,advance_width:advance,line_height:lineHeight,
        bounds,
        source:r.component ? {component_id:r.component.id,text_id:r.text.id,
          component:textAssetNodeState(r.component),instances:r.instances.map(i=>textAssetNodeState(i))} : null});
      text.remove();cleaned.push(text.id);
    }
  } catch(error) {
    failure=error;
    throw error;
  } finally {
    cleaned.length=0;
    const pending=[];
    for(const node of temporary.slice().reverse()) {
      try{if(!node.removed)node.remove();cleaned.push(node.id);}catch(e){pending.push(node.id);}
    }
    if(failure){failure.createdNodeIds=created;failure.cleanedNodeIds=cleaned;failure.uncleanedNodeIds=pending;}
    else if(pending.length) throw Object.assign(Error('临时节点清理失败'),{createdNodeIds:created,cleanedNodeIds:cleaned,uncleanedNodeIds:pending});
  }
  return {schema_version:1,input:cloneAssetValue(input),records,signature:inspected.signature,cleanedNodeIds:cleaned};
}

function planTextAssets(measurement) {
  const records=measurement.records;
  if(!Array.isArray(records) || !records.length || !measurement.signature) throw Error('缺少完整测量和源签名');
  const paths=new Map();
  for(const r of records) {
    if(!textAssetKinds.has(r.kind) || !r.bounds || ![r.bounds.x,r.bounds.y,r.bounds.width,r.bounds.height,r.advance_width,r.line_height].every(Number.isFinite) ||
       r.bounds.width<=0 || r.bounds.height<=0 || r.advance_width<=0 || r.line_height<=0 ||
       !r.entry?.characters || /[\r\n]/.test(r.entry.characters) || paths.has(r.entry.path)) throw Error('字形测量不完整、非单行或路径重复');
    paths.set(r.entry.path,r);
  }
  const groups=new Map();
  for(const r of records) {
    const key=r.family_id;
    if(!groups.has(key)) groups.set(key,[]);
    groups.get(key).push(r);
    if(r.entry.pair_path) {
      const pair=paths.get(r.entry.pair_path);
      if(!pair || pair.entry.characters!==r.entry.characters || pair.aod===r.aod) throw Error('亮屏/AOD配对缺失或字符错误');
    }
  }
  // 配对族共同纵向包络，保留标点的字体基线。
  const groupFor=r=>{
    const linked=new Set([r.family_id]);let changed=true;
    while(changed) {changed=false;for(const q of records) if(q.entry.pair_path) {
      const peer=paths.get(q.entry.pair_path);
      if(linked.has(q.family_id) || linked.has(peer.family_id)) for(const key of [q.family_id,peer.family_id])
        if(!linked.has(key)){linked.add(key);changed=true;}
    }}
    return records.filter(q=>linked.has(q.family_id));
  };
  const edits=records.map(r=>{
    const group=groupFor(r),top=Math.min(...group.map(q=>q.bounds.y)),bottom=Math.max(...group.map(q=>q.bounds.y+q.bounds.height));
    const firstTop=top+Math.ceil(-top*10-1e-7)/10;
    const height=assetRound(bottom-top+firstTop,4),textY=assetGrid(-top+(height-(bottom-top))/2,-top,height-bottom);
    const peers=records.filter(q=>q===r || q.entry.pair_path===r.entry.path || r.entry.pair_path===q.entry.path);
    let w=Math.max(...peers.map(assetMinimumWidth));
    if(['second','data','date'].includes(r.kind) && /^[0-9]$/.test(r.entry.characters))
      w=Math.max(...group.filter(q=>/^[0-9]$/.test(q.entry.characters)).map(assetMinimumWidth));
    const width=assetRound(w,4),tw=assetRound(r.advance_width,2),th=assetRound(r.line_height,2);
    const inkLeft=r.bounds.x+(tw-r.advance_width)/2;
    const textX=assetGrid((width-r.bounds.width)/2-inkLeft,-inkLeft,width-r.bounds.width-inkLeft);
    const localLeft=textX+inkLeft,localTop=textY+r.bounds.y;
    return {entry:r.entry,source:r.source,style:r.style,aod:r.aod,width,height,text_width:tw,text_height:th,
      text_x:textX,text_y:textY,align:'CENTER',stroke_weight:r.aod ? Math.max(2,r.style.stroke_weight || 0) : r.style.stroke_weight || 0,
      margins:[localLeft,localTop,width-localLeft-r.bounds.width,height-localTop-r.bounds.height]};
  });
  return {schema_version:1,measurement,signature:measurement.signature,edits};
}

function assetIntersects(a,b) { return a.x<b.x+b.width-1e-6 && a.x+a.width>b.x+1e-6 && a.y<b.y+b.height-1e-6 && a.y+a.height>b.y+1e-6; }

async function applyTextAssets(plan, api=figma) {
  const rebuilt=planTextAssets(plan.measurement);
  if(assetCanonical(rebuilt)!==assetCanonical(plan)) throw Error('变更计划已改变；重新测量');
  const inspected=await inspectTextInput(plan.measurement.input,api);
  if(inspected.signature!==plan.signature) throw Error('源节点、关联实例或排布已变化；重新测量');
  const created=[],modified=[],preserved=[],componentIds=[],cleaned=plan.measurement.cleanedNodeIds.slice();
  const targets=new Set(inspected.records.map(r=>r.component?.id).filter(Boolean));
  const slots=plan.edits.map((e,i)=>({x:inspected.records[i].component?.x ?? e.entry.x,
    y:inspected.records[i].component?.y ?? e.entry.y,width:e.width,height:e.height}));
  const siblings=inspected.section.children.filter(n=>n.visible && !targets.has(n.id));
  for(let i=0;i<slots.length;i++) {
    if(slots[i].x<0 || slots[i].y<0 || slots[i].x+slots[i].width>inspected.section.width || slots[i].y+slots[i].height>inspected.section.height ||
       siblings.some(n=>assetIntersects(slots[i],n)) || slots.slice(0,i).some(n=>assetIntersects(slots[i],n)))
      throw Error('切图越界或跨族碰撞；保留用户排布，未应用');
  }
  // 所有字体及范围检查在永久写入之前完成。
  for(const edit of plan.edits) await api.loadFontAsync(edit.style.font_name);
  for(let i=0;i<plan.edits.length;i++) {
    const edit=plan.edits[i],{component,text}=inspected.records[i];
    if(component && plan.measurement.input.operation==='create' &&
       (component.width!==edit.width || component.height!==edit.height || text.textAlignHorizontal!=='CENTER' ||
        text.width!==edit.text_width || text.height!==edit.text_height || Math.abs(text.x-edit.text_x)>1e-5 || Math.abs(text.y-edit.text_y)>1e-5 ||
        (edit.aod && (text.fills.length || text.strokeWeight<2))))
      throw Error('已有路径与计划不同；必须使用声明范围的局部修复');
  }
  for(let i=0;i<plan.edits.length;i++) {
    const edit=plan.edits[i],record=inspected.records[i];
    let component=record.component,text=record.text;
    if(component && plan.measurement.input.operation==='create') {
      componentIds.push(component.id);preserved.push(component.id,text.id);continue;
    }
    if(!component) {
      component=api.createComponent();created.push(component.id);component.name=edit.entry.path;
      inspected.section.appendChild(component);component.x=edit.entry.x;component.y=edit.entry.y;component.fills=[];
      text=api.createText();created.push(text.id);await api.loadFontAsync(text.fontName);
      setAssetTextStyle(text,edit.style,edit.aod);text.characters=edit.entry.characters;component.appendChild(text);
    } else {
      modified.push(component.id,text.id);
      if(edit.aod){text.fills=[];text.strokes=cloneAssetValue(assetGray);text.strokeWeight=edit.stroke_weight;text.strokeAlign='CENTER';}
    }
    component.resize(edit.width,edit.height);component.clipsContent=false;
    componentIds.push(component.id);
    component.exportSettings=[{format:'PNG',constraint:{type:'SCALE',value:1},suffix:''}];
    text.textAutoResize='NONE';text.resize(edit.text_width,edit.text_height);
    text.textAlignHorizontal='CENTER';text.textAlignVertical='TOP';text.x=edit.text_x;text.y=edit.text_y;
    for(const instance of record.instances) {
      const before=edit.source.instances.find(n=>n.id===instance.id);
      if(instance.width!==edit.width || instance.height!==edit.height) instance.resize(edit.width,edit.height);
      instance.x=before.x;instance.y=before.y;modified.push(instance.id);
    }
  }
  return {createdNodeIds:created,modifiedNodeIds:modified,preservedNodeIds:preserved,cleanedNodeIds:cleaned,
    resource_families:plan.measurement.input.families.map(f=>({name:f.id,
      kind:['time_hour','time_minute'].includes(f.kind) ? 'time' : f.kind,
      baseline_policy:'shared_font_baseline',node_ids:plan.edits.map((e,i)=>({e,id:componentIds[i]})).filter(v=>f.entries.some(e=>e.path===v.e.entry.path)).map(v=>v.id)})),
    resourceSectionId:inspected.section.id,entries:plan.edits.map((e,i)=>({path:e.entry.path,
      component_id:componentIds[i],width:e.width,height:e.height,margins:e.margins}))};
}

if(typeof module!=='undefined') module.exports={measureTextAssets,planTextAssets,applyTextAssets,checkRepairScope,assetFontAvailable,assetMeasureBounds,prepareAssetMeasurementText};

// Shared by the Skill's Figma setup step and the offline desktop plugin.
const WATCH_PREVIEW_NS = 'watchface.preview';
function inside(node, parent) {
  for (let n = node; n; n = n.parent) if (n.id === parent.id) return true;
  return false;
}

function hasFrameArtwork(node) {
  if (node.visible === false || node.opacity === 0) return false;
  for (const key of ['fills','strokes']) {
    if (key in node && Array.isArray(node[key]) && node[key].some(p =>
      p.visible !== false && p.opacity !== 0 && (p.type !== 'IMAGE' || p.imageHash))) return true;
  }
  return 'children' in node && node.children.some(hasFrameArtwork);
}

async function currentArtwork(api, host, binding) {
  if (!binding.componentId) {
    if (binding.sourceId===host.id) return host;
    const artwork=await api.getNodeByIdAsync(binding.sourceId);
    if (!artwork || artwork.parent.id!==host.id) throw Error('原亮屏组件已移出固定外框。');
    return artwork;
  }
  const candidates=[];
  for (const child of host.children) {
    if (child.type!=='INSTANCE' || child.width!==host.width || child.height!==host.height) continue;
    const component=await child.getMainComponentAsync();
    if (component && component.id===binding.componentId) candidates.push(child);
  }
  if (candidates.length>1) throw Error('亮屏外框内有多个同源同尺寸实例，请只保留用于展示的一个。');
  if (!candidates.length) throw Error('亮屏外框内缺少原规格的完整亮屏实例。');
  return candidates[0];
}

function currentLayerId(binding, source, id) {
  if (!binding.componentId || source.id===binding.sourceId) return id;
  const prefix='I'+binding.sourceId+';';
  return id.startsWith(prefix) ? 'I'+source.id+';'+id.slice(prefix.length) : id;
}

async function inspectWatchBinding(api, main, b, previousOwner) {
  if (!b || !['local', 'full'].includes(b.mode)) throw Error('仅为已确认的动态背景绑定视频。');
  if (!main || main.id !== b.mainId) throw Error('此亮屏稿是副本或绑定已失效，请重新绑定本规格。');
  if (!['INSTANCE','FRAME','COMPONENT'].includes(main.type)) throw Error('请选择完整亮屏画框。');
  if (!Number.isInteger(b.fps) || b.fps <= 0) throw Error('请记录已确认的正整数帧率。');
  if (![main.width,main.height].every(v => Number.isInteger(v) && v > 0 && v % 2 === 0))
    throw Error('H.264 原生画面宽高必须是正偶数。');
  const source = b.version===2 ? await currentArtwork(api,main,b) : main;
  const get = id => typeof id === 'string' ? api.getNodeByIdAsync(id) : null;
  const [scope,board,boundSlot,preview] = await Promise.all([b.scopeId,b.boardId,currentLayerId(b,source,b.slotId),b.previewId].map(get));
  if (!scope || !board || !inside(board,scope) || !inside(main,scope) || !preview || !inside(preview,scope)
      || inside(preview,main) || inside(main,preview) || inside(preview,board))
    throw Error('亮屏稿、切图区或视频展示位已移出本规格分区。');
  if (!['RECTANGLE','FRAME'].includes(preview.type) || ('children' in preview && preview.children.length)
      || preview.width !== main.width || preview.height !== main.height)
    throw Error('视频展示位必须是与亮屏同尺寸的专用空画框或矩形。');
  const owner = preview.getSharedPluginData(WATCH_PREVIEW_NS,'owner');
  if (owner && owner !== main.id && owner !== previousOwner) throw Error('视频展示位属于另一个规格。');
  if (!Array.isArray(b.frameIds) || b.frameIds.length !== 60 || new Set(b.frameIds).size !== 60)
    throw Error('正式动态表盘需要 60 个独立的帧母件。');
  const frames = await Promise.all(b.frameIds.map(get));
  const prefix = frames[0] && frames[0].name.match(/^(.+)\/Ani\/000$/);
  for (const [i, n] of frames.entries()) {
    const label = String(i).padStart(3,'0');
    if (!n || !prefix || n.type !== 'COMPONENT' || !inside(n,board) || n.name !== prefix[1]+'/Ani/'+label)
      throw Error('帧 '+label+' 缺失、改名或已移出本规格切图区。');
    if (!hasFrameArtwork(n)) throw Error('帧 '+label+' 缺少可渲染的图像。');
    if (n.width !== frames[0].width || n.height !== frames[0].height)
      throw Error('帧 '+label+' 与首帧的裁框不一致。');
  }
  if (b.mode === 'full' && (frames[0].width !== main.width || frames[0].height !== main.height))
    throw Error('全屏帧与亮屏稿尺寸不一致。');
  let slot=boundSlot;
  if (!slot && b.version===2 && b.componentId) {
    // Detaching a nested layout changes descendant IDs but preserves its frame instance.
    for (const instance of source.findAllWithCriteria({types:['INSTANCE']})) {
      const component=await instance.getMainComponentAsync();
      if (!component || component.id!==frames[0].id) continue;
      if (slot) throw Error('亮屏稿中有多个实例引用本配色的动画首帧，无法确定动画槽。');
      slot=instance;
    }
  }
  if (!slot || !inside(slot,source) || slot.id === main.id || !('fills' in slot))
    throw Error('找不到亮屏稿中绑定的动画槽。');
  const covers = await Promise.all((b.coverIds || []).map(id=>get(currentLayerId(b,source,id))));
  if (covers.some(n => !n || !inside(n,main) || n.id === slot.id || n.type !== 'RECTANGLE'))
    throw Error('用于展示的高清替身已变化，请重新绑定。');
  return {binding:b, main, source, scope, board, slot, preview, frames, covers};
}

async function bindWatchPreview(api, binding) {
  const main = await api.getNodeByIdAsync(binding.mainId);
  const checked = await inspectWatchBinding(api,main,binding);
  const ready=await ensureWatchHost(api,checked);
  return {mainId:ready.main.id,previewId:ready.preview.id,frames:ready.frames.length};
}

// An existing presentation FRAME can already own the button. For an instance,
// move the actual artwork into a transparent frame in the same stacking slot.
async function ensureWatchHost(api, checked) {
  const {main,slot,scope,preview,binding}=checked;
  if (binding.version===2) return checked;
  let source=main;
  if (main.type==='FRAME') {
    source=main.children.find(n=>n.type==='INSTANCE' && n.id!==slot.id &&
      n.width===main.width && n.height===main.height && inside(slot,n)) || main;
  }
  const component=source.type==='INSTANCE' ? await source.getMainComponentAsync() : null;
  if (main.removed || !inside(main,scope)) throw Error('亮屏在升级期间已经变化。');
  // A concurrent run may already have completed the migration.
  const parent=main.parent;
  const parentRecord=JSON.parse(parent.getSharedPluginData(WATCH_PREVIEW_NS,'binding') || 'null');
  if (parentRecord && parentRecord.version===2 && parentRecord.mainId===parent.id && parentRecord.previewId===preview.id)
    return inspectWatchBinding(api,parent,parentRecord);
  const owner=preview.getSharedPluginData(WATCH_PREVIEW_NS,'owner');
  if (owner && owner!==main.id) throw Error('视频展示位的归属已变化，请重新选择亮屏。');

  let host=main;
  const index=parent.children.findIndex(n=>n.id===main.id);
  const transform=main.relativeTransform;
  const width=main.width, height=main.height;
  const constraints=main.constraints;
  const auto=parent.layoutMode && parent.layoutMode!=='NONE';
  const layout=auto && {position:main.layoutPositioning,h:main.layoutSizingHorizontal,v:main.layoutSizingVertical};
  const oldBox=main.getSharedPluginData(WATCH_PREVIEW_NS,'binding');
  const oldScope=scope.getSharedPluginData(WATCH_PREVIEW_NS,scopeBindingKey(main));
  const restoreLayout=n=>{
    if (!layout) return;
    n.layoutPositioning=layout.position;
    n.layoutSizingHorizontal=layout.h;
    n.layoutSizingVertical=layout.v;
  };
  try {
    if (main.type!=='FRAME') {
      host=api.createFrame();
      host.name=scope.name+' · 亮屏';
      host.fills=[];host.strokes=[];host.clipsContent=main.clipsContent;
      // The artwork already clips its own corners. Keep the frame transparent.
      for (const key of ['topLeftRadius','topRightRadius','bottomLeftRadius','bottomRightRadius','cornerSmoothing'])
        host[key]=main[key];
      host.resize(width,height);
      host.exportSettings=main.exportSettings;
      parent.insertChild(index,host);
      if (constraints) host.constraints=constraints;
      restoreLayout(host);
      host.relativeTransform=transform;
      host.appendChild(main);
      if (main.width!==width || main.height!==height) main.resize(width,height);
      main.relativeTransform=[[1,0,0],[0,1,0]];
    }
    const next={...checked,main:host,source,binding:{...binding,version:2,mainId:host.id,
      sourceId:source.id,componentId:component && component.id}};
    if (host===main && component) await currentArtwork(api,host,next.binding);
    saveWatchBinding(next);
    if (host!==main) {
      main.setSharedPluginData(WATCH_PREVIEW_NS,'binding','');
      scope.setSharedPluginData(WATCH_PREVIEW_NS,scopeBindingKey(main),'');
    }
    return next;
  } catch (error) {
    if (host!==main) {
      if (main.parent.id===host.id) {
        parent.insertChild(index,main);
        if (main.width!==width || main.height!==height) main.resize(width,height);
        restoreLayout(main);main.relativeTransform=transform;
      }
      scope.setSharedPluginData(WATCH_PREVIEW_NS,scopeBindingKey(host),'');
      host.remove();
    }
    main.setSharedPluginData(WATCH_PREVIEW_NS,'binding',oldBox);
    scope.setSharedPluginData(WATCH_PREVIEW_NS,scopeBindingKey(main),oldScope);
    preview.setSharedPluginData(WATCH_PREVIEW_NS,'owner',owner);
    throw error;
  }
}

function saveWatchBinding({main,scope,preview,binding}) {
  const raw=JSON.stringify({...binding, displayParentId:main.parent.id});
  const changes=[[scope,scopeBindingKey(main),raw],[main,'binding',raw],[preview,'owner',main.id]];
  const applied=[];
  try {
    for (const [node,key,value] of changes) {
      const before=node.getSharedPluginData(WATCH_PREVIEW_NS,key);
      if (before===value) continue;
      node.setSharedPluginData(WATCH_PREVIEW_NS,key,value);
      applied.push([node,key,before]);
    }
  } catch(error) {
    for (const [node,key,before] of applied.reverse()) node.setSharedPluginData(WATCH_PREVIEW_NS,key,before);
    throw error;
  }
}

function scopeBindingKey(main) {
  return 'main-' + main.id;
}

function readWatchBinding(main) {
  if (!main) return '';
  const local = main.getSharedPluginData(WATCH_PREVIEW_NS,'binding');
  if (local) return local;
  const key = scopeBindingKey(main);
  for (let parent=main.parent; parent; parent=parent.parent) {
    const backup = parent.getSharedPluginData(WATCH_PREVIEW_NS,key);
    if (backup) return backup;
    if (parent.type === 'PAGE') break;
  }
  return '';
}

// Instance descendant IDs keep the component-layer suffix when Figma replaces the instance root.
async function replacementBinding(api, selected, scope, old, preview) {
  const prefix='I'+old.mainId+';';
  if (!old.slotId.startsWith(prefix)) return null;
  const suffix=old.slotId.slice(prefix.length);
  let master=await api.getNodeByIdAsync(suffix.split(';')[0]);
  while (master && master.type!=='COMPONENT') master=master.parent;
  const parent=await api.getNodeByIdAsync(old.displayParentId || scope.id);
  if (!master || !parent || !inside(parent,scope) || !('children' in parent)) return null;
  const sameSize=parent.children.filter(n=>n.type==='INSTANCE' && n.width===preview.width && n.height===preview.height);
  const candidates=[];
  for (const n of sameSize) {
    const component=await n.getMainComponentAsync();
    if (component && component.id===master.id) candidates.push(n);
  }
  if (selected.id!==preview.id && !candidates.some(n=>n.id===selected.id)) return null;
  if (candidates.length>1) throw Error('此规格有多个同尺寸的亮屏实例，无法确定原展示位。');
  if (!candidates.length) return null;
  const main=candidates[0];
  const remap=id=>id.startsWith(prefix)?'I'+main.id+';'+id.slice(prefix.length):id;
  const binding={...old,mainId:main.id,slotId:remap(old.slotId),coverIds:(old.coverIds || []).map(remap)};
  return inspectWatchBinding(api,main,binding,old.mainId);
}

async function resolveWatchPreview(api, selected) {
  const raw=readWatchBinding(selected);
  const local=raw && JSON.parse(raw);
  if (local && local.mainId===selected.id) return inspectWatchBinding(api,selected,local);

  const parent=selected && selected.parent;
  const outer=parent && parent.getSharedPluginData(WATCH_PREVIEW_NS,'binding');
  if (outer) {
    const b=JSON.parse(outer);
    if (b.version===2 && b.mainId===parent.id && selected.type==='INSTANCE') {
      const checked=await inspectWatchBinding(api,parent,b);
      if (checked.source.id===selected.id) return checked;
    }
  }
  const matches=[];
  for (let scope=selected && selected.parent;scope && scope.type!=='PAGE';scope=scope.parent) {
    for (const key of scope.getSharedPluginDataKeys(WATCH_PREVIEW_NS).filter(k=>k.startsWith('main-'))) {
      const record=scope.getSharedPluginData(WATCH_PREVIEW_NS,key);
      const old=JSON.parse(record);
      if (old.scopeId!==scope.id || key!=='main-'+old.mainId) continue;
      const fromVideo=selected.id===old.previewId;
      if (!fromVideo && (old.version===2 || selected.type!=='INSTANCE')) continue;
      const source=await api.getNodeByIdAsync(old.mainId);
      if (source) {
        if (fromVideo) return inspectWatchBinding(api,source,old);
        continue;
      }
      if (old.version===2) continue;
      const preview=await api.getNodeByIdAsync(old.previewId);
      if (!preview || preview.getSharedPluginData(WATCH_PREVIEW_NS,'owner')!==old.mainId) continue;
      const checked=await replacementBinding(api,selected,scope,old,preview);
      if (checked) matches.push({checked,key,record,old});
    }
  }
  if (matches.length>1) throw Error('此亮屏对应多个视频展示位，需确认本规格的绑定。');
  if (matches.length===1) {
    const {checked,key,record,old}=matches[0];
    const {scope,preview,main}=checked;
    if (await api.getNodeByIdAsync(old.mainId) || main.removed || preview.removed ||
        scope.getSharedPluginData(WATCH_PREVIEW_NS,key)!==record ||
        preview.getSharedPluginData(WATCH_PREVIEW_NS,'owner')!==old.mainId)
      throw Error('表盘绑定在恢复期间已变化，请再次更新。');
    saveWatchBinding(checked);
    scope.setSharedPluginData(WATCH_PREVIEW_NS,key,'');
    return checked;
  }
  if (local) throw Error('此亮屏是副本，原规格绑定不能用于副本。');
  throw Error('当前图层未绑定。请选中规格展示区的完整亮屏或已绑定的视频展示位。');
}

// Resolve the current path from the stable bound ID, including newly added or reordered siblings.
function mapToCopy(main, source, copy) {
  const path = [];
  for (let n=source; n.id !== main.id; n=n.parent) path.unshift(n.parent.children.findIndex(c => c.id === n.id));
  let target=copy;
  for (const i of path) target=target && target.children && target.children[i];
  if (!target || target.name !== source.name || target.type !== source.type)
    throw Error('临时稿的图层结构与亮屏稿不一致：'+source.name);
  return target;
}

function previewState(n) {
  return JSON.stringify([n.fills,n.width,n.height,n.getSharedPluginData(WATCH_PREVIEW_NS,'owner')]);
}
function verifyTarget(n, before) {
  if (!n || n.removed || previewState(n)!==before) throw Error('视频展示位在导出期间发生变化，保留较新的版本。');
}

function coverHole(cover, slot) {
  const a=cover.absoluteTransform, b=slot.absoluteTransform;
  const det=a[0][0]*a[1][1]-a[0][1]*a[1][0];
  if (!det) throw Error('高清替身的变换不可逆。');
  const points=[[0,0],[slot.width,0],[0,slot.height],[slot.width,slot.height]].map(([x,y])=>{
    const dx=b[0][0]*x+b[0][1]*y+b[0][2]-a[0][2],dy=b[1][0]*x+b[1][1]*y+b[1][2]-a[1][2];
    return [(a[1][1]*dx-a[0][1]*dy)/det,(-a[1][0]*dx+a[0][0]*dy)/det];
  });
  if (Math.abs(points[0][1]-points[1][1])>.001 || Math.abs(points[0][0]-points[2][0])>.001)
    throw Error('高清替身与动画裁框的相对旋转需要重新确认。');
  const xs=points.map(p=>p[0]),ys=points.map(p=>p[1]);
  const box=[Math.max(0,Math.min(...xs)),Math.max(0,Math.min(...ys)),Math.min(cover.width,Math.max(...xs)),Math.min(cover.height,Math.max(...ys))];
  return box[2]>box[0] && box[3]>box[1] ? box : null;
}
if (typeof module !== 'undefined' && module.exports)
  module.exports={inspectWatchBinding,saveWatchBinding,ensureWatchHost,bindWatchPreview,resolveWatchPreview,mapToCopy,coverHole,previewState,verifyTarget};

const repairPanelHTML="<!doctype html><html lang=\"zh-CN\"><meta charset=\"utf-8\">\n<style>\n*{box-sizing:border-box}body{font:13px/1.45 system-ui,sans-serif;margin:16px;color:var(--figma-color-text,#222);background:var(--figma-color-bg,#fff)}\np{margin:0 0 10px}section{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:0 10px;align-items:center;padding:6px 0;border-bottom:1px solid var(--figma-color-border,#ddd)}\nsmall{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--figma-color-text-secondary,#666)}\nbutton,input{font:inherit;color:inherit;background:var(--figma-color-bg,#fff);border:1px solid var(--figma-color-border-strong,#999);border-radius:5px;padding:4px 8px}\nbutton{cursor:pointer}input{width:62px}footer,.settings{display:flex;gap:8px;align-items:center;margin-top:10px}\nfooter{justify-content:flex-end}.primary{background:var(--figma-color-bg-brand,#0c75d8);color:white;border-color:transparent}\n#message{margin:6px 0;color:var(--figma-color-text-danger,#a42a18)}#message:empty{display:none}\n</style>\n<p>在画布或左侧图层列表选择图层，再点对应行的「使用选中」。</p>\n<div id=\"rows\"></div>\n<div class=\"settings\"><label for=\"fps\">帧率</label><input type=\"number\" id=\"fps\" value=\"30\" min=\"1\" step=\"1\">fps</div>\n<p id=\"message\" role=\"status\"></p>\n<footer><button id=\"cancel\">取消</button><button class=\"primary\" id=\"save\">保存绑定并导出 MP4</button></footer>\n<script>\nconst post=message=>parent.postMessage({pluginMessage:message},'*');\nconst names={main:'完整亮屏外框',slot:'亮屏内的动画图层',first:'切图区的首帧母件（Ani/000）',preview:'整表视频展示位',covers:'高清替身（可选）'};\nconst fields={};\nfor(const [role,name] of Object.entries(names)){\n  const row=document.createElement('section'),label=document.createElement('div'),value=document.createElement('small'),buttons=document.createElement('div');\n  label.append(name,value);fields[role]=value;\n  if(role==='covers'){const clear=document.createElement('button');clear.textContent='清空';clear.onclick=()=>post({kind:'clear-covers'});buttons.append(clear);}\n  const pick=document.createElement('button');pick.textContent='使用选中';pick.setAttribute('aria-label',name+'：使用选中');\n  pick.onclick=()=>post({kind:'pick',role});buttons.append(pick);row.append(label,buttons);document.getElementById('rows').append(row);\n}\nconst fps=document.getElementById('fps'),save=document.getElementById('save');\ndocument.getElementById('cancel').onclick=()=>post({kind:'cancel'});\nsave.onclick=()=>{save.disabled=true;post({kind:'save',fps:fps.value});};\nwindow.onmessage=e=>{\n  const m=e.data.pluginMessage;if(!m || !m.labels)return;\n  for(const [role,name] of Object.entries(m.labels)){fields[role].textContent=name;fields[role].title=name;}\n  if(m.resetFps)fps.value=m.fps;\n  document.getElementById('message').textContent=m.message || '';save.disabled=false;\n};\npost({kind:'ready'});\n</script></html>\n";
// Read even an incomplete binding so the designer only replaces the broken references.
function previewRegistration(node) {
  const owner=node.getSharedPluginData(WATCH_PREVIEW_NS,'owner');
  for (let p=node.parent;p && p.type!=='PAGE';p=p.parent) {
    const keys=owner ? ['main-'+owner] : p.getSharedPluginDataKeys(WATCH_PREVIEW_NS).filter(key=>key.startsWith('main-'));
    for (const key of keys) {
      const b=JSON.parse(p.getSharedPluginData(WATCH_PREVIEW_NS,key) || 'null');
      if (b && b.previewId===node.id && key==='main-'+b.mainId && b.scopeId===p.id) return b;
    }
  }
  return null;
}

async function repairDraft(api,selected) {
  let record=null;
  for (let n=selected;n && n.type!=='PAGE';n=n.parent) {
    const raw=readWatchBinding(n);
    if (raw) {
      const b=JSON.parse(raw);
      if (b.mainId===n.id) {record=b;break;}
    }
    record=previewRegistration(n);
    if (record) break;
  }
  const get=id=>id ? api.getNodeByIdAsync(id) : null;
  const main=record ? await get(record.mainId) : selected;
  const host=main && ['FRAME','INSTANCE'].includes(main.type) ? main : null;
  let source=host;
  if (host && record && record.version===2) {
    try {source=await currentArtwork(api,host,record);} catch(e) {source=null;}
  }
  const layer=id=>source ? currentLayerId(record,source,id) : id;
  return {
    record, raw:host ? readWatchBinding(host) : '',
    main:host, slot:record && await get(layer(record.slotId)),
    first:record && await get(record.frameIds[0]), preview:record && await get(record.previewId),
    covers:record ? await Promise.all((record.coverIds || []).map(id=>get(layer(id)))) : [],
    fps:record ? record.fps : 30
  };
}

function collectRepairFrames(first,scope,oldBoard) {
  const match=first.name.match(/^(.+\/Ani\/)000$/);
  if (first.type!=='COMPONENT' || !match) throw Error('请从切图区选中名为 …/Ani/000 的首帧主组件。');
  let missing='000';
  for (let board=first.parent;board && board.id!==scope.id && board.type!=='PAGE';board=board.parent) {
    const byName=new Map();
    for (const n of board.findAllWithCriteria({types:['COMPONENT']})) {
      if (!n.name.startsWith(match[1])) continue;
      if (byName.has(n.name)) byName.set(n.name,null);
      else byName.set(n.name,n.id);
    }
    const ids=[];
    for (let i=0;i<60;i++) {
      const label=String(i).padStart(3,'0'),name=match[1]+label;
      if (byName.get(name)===null) throw Error('切图区有多个 '+name+' 主组件。');
      if (!byName.has(name)) {missing=label;break;}
      ids.push(byName.get(name));
    }
    if (ids.length===60 && ids[0]===first.id) return {boardId:board.id,frameIds:ids};
    if (oldBoard && oldBoard.id===board.id) break;
  }
  throw Error('切图区缺少同一套动画的 '+missing+' 帧；请将 000–059 放在同一切图区。');
}

async function commitRepair(api,draft) {
  const {main,slot,first,preview,covers,record}=draft;
  if (!main || main.removed || !['FRAME','INSTANCE'].includes(main.type)) throw Error('请选完整的亮屏外框或实例。');
  if (!slot || slot.removed || slot.id===main.id || !inside(slot,main) || !['INSTANCE','RECTANGLE','FRAME'].includes(slot.type))
    throw Error('请在这个亮屏内部选择播放动画的图层。');
  if (!first || first.removed || first.type!=='COMPONENT' || !/\/Ani\/000$/.test(first.name))
    throw Error('请在切图区选择本配色的 Ani/000 首帧主组件。');
  if (slot.type==='INSTANCE') {
    const component=await slot.getMainComponentAsync();
    if (!component || component.id!==first.id) throw Error('动画实例和所选首帧不匹配，请选择同一配色的 Ani/000。');
  } else if ('children' in slot && slot.children.length) {
    throw Error('动画占位请选择首帧实例或单独的图片图层。');
  }
  if (!preview || preview.removed || preview.id===main.id || inside(preview,main) || inside(main,preview))
    throw Error('请在亮屏外选择独立的视频展示位。');
  if (!['FRAME','RECTANGLE'].includes(preview.type) || !Array.isArray(preview.fills) ||
      (preview.fills.some(p=>p.type!=='VIDEO' && p.type!=='SOLID') && !(record && preview.id===record.previewId)))
    throw Error('新视频展示位请选择已有视频或专用空画框、空矩形。');
  if (covers.some(n=>!n || n.removed || n.type!=='RECTANGLE' || !inside(n,main) || n.id===slot.id))
    throw Error('高清替身已变化，请重选对应矩形；稿内已移除替身时请点「清空」。');

  const oldScope=record && await api.getNodeByIdAsync(record.scopeId);
  if (record && record.mainId!==main.id && await api.getNodeByIdAsync(record.mainId))
    throw Error('原亮屏仍在使用这份绑定，请从要修改的亮屏重新打开插件。');
  let scope=main.parent;
  while (scope && scope.type!=='PAGE' && !(inside(first,scope) && inside(preview,scope))) scope=scope.parent;
  if (oldScope && inside(main,oldScope)) {
    if (!inside(preview,oldScope) || !inside(first,oldScope)) throw Error('视频和帧母件必须位于原亮屏的规格分区内。');
    scope=oldScope;
  }
  if (!scope || scope.type==='PAGE' || scope.type==='DOCUMENT') throw Error('请把亮屏、切图区和视频展示位放进同一个规格分区。');
  let board=record && await api.getNodeByIdAsync(record.boardId);
  if (board && (!inside(first,board) || !inside(board,scope))) board=null;
  const sequence=collectRepairFrames(first,scope,board);
  const owner=preview.getSharedPluginData(WATCH_PREVIEW_NS,'owner');
  const prior=previewRegistration(preview);
  const priorScope=prior && await api.getNodeByIdAsync(prior.scopeId);
  const registeredOwner=prior ? prior.mainId : owner;
  if (registeredOwner && registeredOwner!==main.id) {
    const ownerNode=await api.getNodeByIdAsync(registeredOwner);
    const raw=ownerNode && readWatchBinding(ownerNode);
    if (ownerNode && (prior || !raw || JSON.parse(raw).previewId===preview.id))
      throw Error('这个视频展示位属于另一块亮屏，请选择本规格的视频。');
  }
  const oldPreview=record && await api.getNodeByIdAsync(record.previewId);
  const binding={...sequence,mainId:main.id,scopeId:scope.id,previewId:preview.id,slotId:slot.id,
    coverIds:covers.map(n=>n.id),fps:draft.fps,mode:record ? record.mode : (first.width===main.width && first.height===main.height?'full':'local')};
  // A fixed host may now contain a rebuilt instance or a detached frame.
  if (main.type==='FRAME') {
    const child=main.children.find(n=>n.type==='INSTANCE' && n.id!==slot.id && inside(slot,n) && n.width===main.width && n.height===main.height);
    const component=child && await child.getMainComponentAsync();
    Object.assign(binding,{version:2,sourceId:child ? child.id : main.id,componentId:component ? component.id : null});
  }
  const checked=await inspectWatchBinding(api,main,binding,owner);
  if (slot.type!=='INSTANCE' && checked.frames.some(frame=>!canUseFrameFill(frame,slot)))
    throw Error('这套动画含有复合帧，请选亮屏内的首帧组件实例。');
  if (checked.slot.id!==slot.id || checked.frames[0].id!==first.id || main.removed || slot.removed || preview.removed ||
      readWatchBinding(main)!==draft.raw || preview.getSharedPluginData(WATCH_PREVIEW_NS,'owner')!==owner)
    throw Error('绑定在选择期间发生变化，请重新打开「重新绑定」。');
  // All selections are validated before changing shared records or registering buttons.
  let ready=checked;
  if (binding.version===2) saveWatchBinding(checked);
  else {
    if (owner && owner!==main.id) throw Error('请将亮屏实例套入同尺寸画框，再选外框重新绑定。');
    ready=await ensureWatchHost(api,checked);
  }
  if (oldPreview && oldPreview.id!==preview.id && oldPreview.getSharedPluginData(WATCH_PREVIEW_NS,'owner')===record.mainId) {
    oldPreview.setSharedPluginData(WATCH_PREVIEW_NS,'owner','');
    oldPreview.setRelaunchData({});
  }
  for (const [previous,section] of [[record,oldScope],[prior,priorScope]]) {
    if (!section || (section.id===scope.id && previous.mainId===ready.main.id)) continue;
    const key='main-'+previous.mainId;
    if (section.getSharedPluginData(WATCH_PREVIEW_NS,key)===JSON.stringify(previous))
      section.setSharedPluginData(WATCH_PREVIEW_NS,key,'');
  }
  return ready;
}

async function openRepair(selected,reason) {
  let draft=await repairDraft(figma,selected);
  let busy=false;
  const roles=['main','slot','first','preview','covers'];
  function display(message,resetFps=false) {
    const labels={};
    for (const role of roles) {
      const nodes=role==='covers'?draft.covers:[draft[role]];
      labels[role]=nodes.map(n=>n && !n.removed?n.name:'待选择').join('、') || '无';
    }
    figma.ui.postMessage({labels,fps:draft.fps,resetFps,message});
  }
  figma.showUI(repairPanelHTML,{width:410,height:460,themeColors:true,title:'重新绑定'});
  figma.ui.onmessage=async m=>{
    if (busy) return;
    busy=true;
    let saved=false;
    try {
      if (m.kind==='ready') {display(reason,true);return;}
      if (m.kind==='cancel') {figma.closePlugin();return;}
      if (m.kind==='pick' && roles.includes(m.role)) {
        const selection=figma.currentPage.selection;
        if (!selection.length || (m.role!=='covers' && selection.length!==1)) throw Error('先在画布或左侧图层列表选中对应图层，再点「使用选中」。');
        if (m.role==='main') {
          if (!['FRAME','INSTANCE'].includes(selection[0].type)) throw Error('亮屏请选择完整画框或实例。');
          if (!draft.main && draft.record && !await figma.getNodeByIdAsync(draft.record.mainId)) {
            draft.main=selection[0];draft.raw=readWatchBinding(draft.main);
            if (draft.raw && JSON.parse(draft.raw).mainId===draft.main.id) draft=await repairDraft(figma,draft.main);
          } else {draft=await repairDraft(figma,selection[0]);}
        } else draft[m.role]=m.role==='covers' ? selection.slice() : selection[0];
        display('',m.role==='main');return;
      }
      if (m.kind==='clear-covers') {draft.covers=[];display('');return;}
      if (m.kind==='save') {
        draft.fps=Number(m.fps);
        const next=await commitRepair(figma,draft);
        saved=true;
        figma.currentPage.selection=[next.main];
        figma.ui.close();
        await renderPreview(next);
      }
    } catch(e) {
      removeScratch();
      if (saved) figma.closePlugin('绑定已保存。'+String(e.message || e));
      else display(String(e.message || e));
    } finally {busy=false;}
  };
}

const pngOptions = {format:'PNG',constraint:{type:'SCALE',value:1},contentsOnly:true};
const scratch = new Set();
function removeScratch() {
  for (const n of scratch) if (!n.removed) n.remove();
  scratch.clear();
}
figma.on('close',removeScratch);

function own(n) { scratch.add(n); return n; }

function canUseFrameFill(frame,slot) {
  return !frame.children.length && !frame.effects.length && !frame.strokes.length && !frame.cornerRadius &&
    (!('children' in slot) || !slot.children.length);
}

function rectanglePath(x0,y0,x1,y1) {
  return 'M '+x0+' '+y0+' L '+x1+' '+y0+' L '+x1+' '+y1+' L '+x0+' '+y1+' Z';
}

// Keep the registered stand-in's exterior and its original effects in a native mask group.
function cutRepresentative(cover, slot) {
  if (!cover.visible) return;
  const hole=coverHole(cover,slot);
  if (!hole) return;
  if (hole[0]===0 && hole[1]===0 && hole[2]===cover.width && hole[3]===cover.height) {
    cover.visible=false;
    return;
  }
  const parent=cover.parent;
  const position=parent.children.findIndex(n=>n.id===cover.id);
  const transform=cover.relativeTransform;
  const mask=own(figma.createVector());
  mask.vectorPaths=[{windingRule:'EVENODD',data:rectanglePath(0,0,cover.width,cover.height)+' '+rectanglePath(...hole)}];
  mask.strokes=[];
  mask.fills=[{type:'SOLID',color:{r:1,g:1,b:1}}];
  parent.insertChild(position,mask);
  mask.relativeTransform=transform;
  mask.isMask=true;
  mask.maskType='ALPHA';
  figma.group([mask,cover],parent,position);
  scratch.delete(mask); // It now belongs to the managed render copy.
}

function createRenderCopy(main,source) {
  let copy=own(main.clone());
  let page=main;
  while (page.type!=='PAGE') page=page.parent;
  page.appendChild(copy);
  if (copy.type==='INSTANCE') {
    const instance=copy;
    copy=own(instance.detachInstance());scratch.delete(instance);
  }
  if (source.id!==main.id && source.type==='INSTANCE')
    mapToCopy(main,source,copy).detachInstance();
  copy.name='临时 · 整表视频渲染';
  copy.x=main.absoluteTransform[0][2]+main.width+48;
  copy.y=main.absoluteTransform[1][2];
  return copy;
}

function openEncoder() {
  let pending;
  function wait(kind,message) {
    return new Promise((resolve,reject)=>{
      pending={kind,resolve,reject};
      if (message) figma.ui.postMessage(message);
    });
  }
  const ready=wait('ready');
  figma.showUI(__html__,{width:312,height:94,themeColors:true,title:'更新整表 MP4'});
  figma.ui.onmessage=m=>{
    if (!pending)return;
    if (m.kind==='error') pending.reject(Error(m.message));
    else if (m.kind===pending.kind) pending.resolve(m);
    else pending.reject(Error('编码器返回了意外的帧序。'));
    pending=null;
  };
  return {ready,wait};
}

async function updatePreview() {
  figma.skipInvisibleInstanceChildren=false;
  const selected=figma.currentPage.selection;
  const entry=selected.length===1 ? selected[0] : null;
  if (figma.command==='rebind') return openRepair(entry,'');
  let current;
  try {
    if (!entry) throw Error('请选中一个已绑定的动态表盘亮屏稿。');
    current=await ensureWatchHost(figma,await resolveWatchPreview(figma,entry));
  } catch(e) {
    return openRepair(entry,String(e.message || e));
  }
  if (entry.id!==current.preview.id && entry.id!==current.main.id) figma.currentPage.selection=[current.main];
  return renderPreview(current);
}

async function renderPreview(current) {
  const {main,source,slot,preview,frames,covers,binding}=current;
  const initial=previewState(preview);
  const initialBinding=readWatchBinding(main);
  const rebind='手动选择本规格的动画图层和视频展示位';
  main.setRelaunchData({update:'按当前亮屏稿和已绑定的 PNG 序列生成完整视频',rebind});
  preview.setRelaunchData({update:'按本规格当前亮屏更新视频，重置亮屏后也可从这里运行',rebind});
  const encoder=openEncoder();await encoder.ready;
  await encoder.wait('ack',{kind:'start',name:main.name,width:main.width,height:main.height,fps:binding.fps,count:frames.length});
  const copy=createRenderCopy(main,source);
  const dst=mapToCopy(main,slot,copy);
  const mappedCovers=covers.map(n=>mapToCopy(main,n,copy));
  for (const cover of mappedCovers) await cutRepresentative(cover,dst);
  const width=dst.width, height=dst.height;
  for (let index=0;index<frames.length;index++) {
    const frame=frames[index];
    if (frame.removed || !inside(frame,current.board)) throw Error('找不到帧 '+String(index).padStart(3,'0')+'。');
    // Reuse already-resident artwork. Newly created image hashes can export blank in the desktop renderer.
    if (canUseFrameFill(frame,dst)) {
      dst.fills=frame.fills;
    } else if (dst.type==='INSTANCE') {
      dst.swapComponent(frame);
      dst.resize(width,height);
    } else {
      throw Error('复合动画帧需要绑定亮屏中的母件实例。');
    }
    const full=await copy.exportAsync(pngOptions);
    await encoder.wait('ack',{kind:'frame',index,bytes:full});
  }
  removeScratch();
  const encoded=await encoder.wait('encoded',{kind:'finish'});
  const bytes=new Uint8Array(encoded.bytes);
  if (String.fromCharCode(...bytes.slice(4,8))!=='ftyp') throw Error('编码器没有返回 MP4。');
  function assertCurrent() {
    if(main.removed || readWatchBinding(main)!==initialBinding)
      throw Error('亮屏绑定在导出期间已变化。');
    if (source.removed || !inside(source,main)) throw Error('渲染期间亮屏实例被替换，请再次更新。');
    verifyTarget(preview,initial);
    if(!inside(preview,current.scope))throw Error('视频展示位已经移动到另一个规格。');
  }
  let video;
  try {
    assertCurrent();
    video=await figma.createVideoAsync(bytes);
    // After the asynchronous import, validate before the single mutation that replaces the previous video.
    assertCurrent();
    const latest=await resolveWatchPreview(figma,main);
    if (latest.source.id!==source.id || latest.slot.id!==slot.id)
      throw Error('渲染期间亮屏或动画槽已更换，请再次更新。');
    assertCurrent();
    preview.fills=[{type:'VIDEO',scaleMode:'FIT',videoHash:video.hash}];
  } catch(e) {
    figma.ui.resize(350,155);
    figma.ui.postMessage({kind:'download',message:'MP4 已生成，回填未完成。'+String(e.message || e)});
    return;
  }
  figma.closePlugin('整表 MP4 已更新 · '+frames.length+' 帧 · '+binding.fps+' fps');
}
updatePreview().catch(e=>{
  removeScratch();
  figma.closePlugin(String(e.message || e));
});

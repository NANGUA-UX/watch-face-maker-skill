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

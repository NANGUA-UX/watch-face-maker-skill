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

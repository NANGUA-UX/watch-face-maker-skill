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

/**
 * 在 Figma use_figma 中调用：await collectSnapshot(config)
 * 只读指定 section；分页避免工具输出被截断。
 */
async function collectSnapshot(config) {
  const required = [
    "file_key", "section_id", "asset_board_id", "main_id",
    "active_id", "aod_id", "preview_id",
  ];
  const missing = required.filter((key) => !config || !config[key]);
  if (missing.length) throw new Error(`缺少配置：${missing.join(", ")}`);
  const profile = config.profile || "full";
  if (!["full", "validation", "preflight"].includes(profile)) throw new Error("未知采集模式");

  const section = await figma.getNodeByIdAsync(config.section_id);
  if (!section) throw new Error(`找不到 section：${config.section_id}`);
  const allNodes = [];
  const visit = (node) => {
    allNodes.push(node);
    if ("children" in node && !(config.skip_instance_children && node.type === "INSTANCE")) {
      node.children.forEach(visit);
    }
  };
  visit(section);

  // 每页都记录整组几何签名，拒绝把编辑中的不同版本拼成完整快照。
  const canonical = (value) => {
    if (Array.isArray(value)) return value.map(canonical);
    if (value && typeof value === "object") {
      return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])]));
    }
    return value;
  };
  const fields = ["x", "y", "width", "height", "visible", "relativeTransform", "fills", "strokes", "opacity",
    "clipsContent", "exportSettings", "effects", "characters", "fontName", "fontSize", "strokeWeight", "strokeAlign",
    "lineHeight", "letterSpacing", "textAlignHorizontal", "textAlignVertical", "vectorPaths", "vectorNetwork",
    "textStyleId", "fillStyleId", "componentProperties", "overrides", "layoutMode", "itemSpacing", "constraints",
    "cornerRadius", "boundVariables", "paddingLeft", "paddingRight", "paddingTop", "paddingBottom",
    "blendMode", "isMask", "maskType", "booleanOperation", "strokeCap", "strokeJoin", "dashPattern",
    "topLeftRadius", "topRightRadius", "bottomLeftRadius", "bottomRightRadius", "cornerSmoothing",
    "strokeTopWeight", "strokeBottomWeight", "strokeLeftWeight", "strokeRightWeight", "arcData", "resolvedVariableModes"];
  const nodeContent = (node, references) => JSON.stringify(canonical([node.id, node.parent?.id, node.name, node.type,
    ...fields.map((key) => key in node ? node[key] : null), references[node.id] || null,
    node.type === "TEXT" ? node.getStyledTextSegments(["fontName", "fontSize", "fills", "textStyleId", "lineHeight", "letterSpacing", "textCase", "textDecoration"]) : null]));
  const hashText = (text, hash = 2166136261) => {
    for (let i = 0; i < text.length; i++) hash = Math.imul(hash ^ text.charCodeAt(i), 16777619) >>> 0;
    return hash;
  };
  const signatureOf = (references = {}) => {
    let hash = 2166136261;
    const scan = (node) => {
      hash = hashText(nodeContent(node, references), hash);
      if ("children" in node && !(config.skip_instance_children && node.type === "INSTANCE")) node.children.forEach(scan);
    };
    scan(section);
    return hash.toString(16);
  };
  const references = async () => {
    const result = {};
    for (const node of allNodes.filter(n => n.type === "INSTANCE")) {
      const component = await node.getMainComponentAsync();
      result[node.id] = component ? {id: component.id, key: component.key, remote: component.remote} : null;
    }
    return result;
  };
  const initial = signatureOf();
  const refsBefore = await references();
  if (initial !== signatureOf()) throw new Error("采集期间内容变化，请重新采集");
  const signature = signatureOf(refsBefore);

  const offset = Number.isInteger(config.offset) && config.offset >= 0 ? config.offset : 0;
  const limit = Number.isInteger(config.limit) && config.limit > 0 ? config.limit : 20;
  const page = allNodes.slice(offset, offset + limit);

  const plain = (value) => {
    if (typeof value === "symbol") return value.description || value.toString();
    if (Array.isArray(value)) return value.map(plain);
    if (value && typeof value === "object") {
      const result = {};
      for (const key of Object.keys(value)) result[key] = plain(value[key]);
      return result;
    }
    return value;
  };
  const bounds = (value) => value ? {
    x: value.x, y: value.y, width: value.width, height: value.height,
  } : null;

  const records = [];
  const imageSizes = new Map();
  const byteLimit = config.max_bytes || 12000;
  let bytes = 0;
  const byteLength = value => encodeURIComponent(JSON.stringify(value)).replace(/%[A-F\d]{2}/gi, "x").length;
  for (const node of page) {
    const record = {
      id: node.id,
      parent_id: node.parent ? node.parent.id : null,
      name: node.name,
      type: node.type,
      visible: "visible" in node ? node.visible : true,
      opacity: "opacity" in node ? node.opacity : 1,
      fills: "fills" in node ? plain(node.fills) : [],
      width: "width" in node ? node.width : null,
      height: "height" in node ? node.height : null,
      x: "x" in node ? node.x : null,
      y: "y" in node ? node.y : null,
      relative_transform: "relativeTransform" in node ? plain(node.relativeTransform) : null,
      absolute_transform: "absoluteTransform" in node ? plain(node.absoluteTransform) : null,
      render_bounds: bounds("absoluteRenderBounds" in node ? node.absoluteRenderBounds : null),
      clips_content: "clipsContent" in node ? node.clipsContent : null,
      export_settings: "exportSettings" in node ? plain(node.exportSettings) : [],
    };
    if (node.type === "INSTANCE") {
      const component = refsBefore[node.id];
      record.main_component_id = component ? component.id : null;
      record.main_component_key = component && "key" in component ? component.key : null;
      record.main_component_remote = component && "remote" in component ? component.remote : null;
    }
    if (node.type === "TEXT") {
      record.text_segments = plain(node.getStyledTextSegments(["fontName", "fontSize", "fills", "textStyleId", "lineHeight", "letterSpacing", "textCase", "textDecoration"]));
      record.characters = node.characters;
      record.font_name = plain(node.fontName);
      record.font_size = plain(node.fontSize);
      record.fills = plain(node.fills);
      record.strokes = plain(node.strokes);
      record.stroke_weight = plain(node.strokeWeight);
      record.stroke_align = node.strokeAlign;
      record.glyph_bounds = bounds(node.absoluteRenderBounds);
    }
    const hashes = [];
    if ("fills" in node && Array.isArray(node.fills)) {
      for (const fill of node.fills) {
        if (fill && fill.type === "IMAGE" && fill.imageHash) hashes.push(fill.imageHash);
      }
    }
    if (hashes.length && profile !== "preflight") {
      record.images = [];
      for (const imageHash of hashes) {
        if (!imageSizes.has(imageHash)) {
          const image = figma.getImageByHash(imageHash);
          imageSizes.set(imageHash, image ? await image.getSizeAsync() : null);
        }
        const size = imageSizes.get(imageHash);
        record.images.push({imageHash, size: plain(size)});
      }
      record.imageHash = hashes.length === 1 ? hashes[0] : hashes;
    }
    if (profile === "full") {
      for (const key of fields) if (key in node) record[key] = plain(node[key]);
    } else {
      // 原始矢量仍参与签名；正式精简快照保留校验字段，避免重复返回大路径和别名。
      const aliases = new Set(["relativeTransform", "clipsContent", "exportSettings", "fontName", "fontSize", "strokeWeight", "strokeAlign"]);
      for (const key of fields) {
        if (!(key in node) || aliases.has(key) || ["vectorPaths", "vectorNetwork"].includes(key)) continue;
        record[key] = plain(node[key]);
      }
      if ("strokeWeight" in node) record.stroke_weight = plain(node.strokeWeight);
      if ("strokeAlign" in node) record.stroke_align = plain(node.strokeAlign);
      record.content_signature = hashText(nodeContent(node, refsBefore)).toString(16);
    }
    const recordBytes = byteLength(record);
    if (bytes + recordBytes > byteLimit) {
      if (!records.length) throw new Error(`单节点超过返回预算：${node.id}，请单独读取或提高 max_bytes`);
      break;
    }
    records.push(record);
    bytes += recordBytes;
  }

  const refsAfter = await references();
  const afterSignature = signatureOf(refsAfter);
  if (signature !== afterSignature) throw new Error("采集期间内容或母件引用变化，请重新采集");
  const nextOffset = offset + records.length;
  return {
    schema_version: 2,
    evidence_level: profile === "preflight" ? "preflight" : "validation",
    stability: {before: signature, after: afterSignature, status: "pass", algorithm: "fnv1a32-content-v3"},
    captured_at: new Date().toISOString(),
    capture_signature: signature,
    config: plain(config),
    nodes: records,
    total_nodes: allNodes.length,
    next_offset: nextOffset < allNodes.length ? nextOffset : null,
    page: {offset, limit, count: records.length},
    traversal: {skip_instance_children: config.skip_instance_children === true},
  };
}

if (typeof module !== "undefined") module.exports = {collectSnapshot};

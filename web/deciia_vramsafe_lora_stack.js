/**
 * 🛡️ Deciia 显存安全 LoRA 串 v1（作者标记 deciia）
 * ============================================================
 * 班长面板 UX（banzhang_lora_loader v2.1）+ Deciia 方式列（v12 内核）合并。
 * 目标节点：DeciiaVramSafeLoraStack（后端 deciia_vramsafe_lora_stack.py）
 *
 * 每行：[启用勾选] [LoRA文件(Filter+文件夹树弹层)] [强度] [⇄方式] [备注] [×删除]
 *   ⇄ 两态：灰空心=标准（权重融合，LoRA only 变体/风格/修复类）
 *           绿实心=旁路（前向叠加不改权重，量化基座 bypass 变体）
 * 底部：[＋ 添加 LoRA] [↑ 上移] [↓ 下移]（点行选中），最多 16 行
 * 面板行(on/file/strength/mode/note)整体序列化进「LoRA配置」STRING widget；
 * 「LoRA文件」「模型强度」兜底 widget 隐藏不删（扩展未加载时仍可单 LoRA 使用）。
 *
 * 方式判定纪律（2026-09-15 修正）：旁路按「变体」定，不按「加速类」一刀切。
 * ============================================================
 */
import { app } from "../../../scripts/app.js";

const NODE_NAME = "DeciiaVramSafeLoraStack";
const MAX_ROWS = 16;
const MODE_STD = "标准";
const MODE_BYPASS = "旁路";

// ── 样式注入（dvz- 前缀，不与班长 bzl- 冲突）────────────────
(function injectCss() {
  const ID = "dvz-lora-css";
  if (document.getElementById(ID)) return;
  const s = document.createElement("style");
  s.id = ID;
  s.textContent = `
.dvz-wrap{width:100%;padding:6px 10px 8px;box-sizing:border-box;font-family:'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif}
.dvz-row{display:flex;align-items:center;gap:6px;margin-bottom:6px;background:rgba(255,255,255,.045);border:1px solid rgba(255,255,255,.09);border-radius:8px;padding:5px 7px;cursor:pointer}
.dvz-row.dvz-off{opacity:.42}
.dvz-row.dvz-sel{border-color:#4a9eff}
.dvz-row input[type="checkbox"]{width:15px;height:15px;accent-color:#4a9eff;cursor:pointer;flex-shrink:0;margin:0}
.dvz-row .dvz-file{flex:1.3;min-width:0;background:#22242a;color:#e8e8e8;border:1px solid #3a3d45;border-radius:6px;padding:4px 6px;font-size:12px;outline:none;cursor:pointer;text-align:left;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-family:inherit}
.dvz-row .dvz-file:hover{border-color:#4a9eff}
.dvz-pop{position:fixed;z-index:99999;background:#1c1e24;border:1px solid #3a3d45;border-radius:10px;box-shadow:0 12px 40px rgba(0,0,0,.55);overflow:hidden;font-family:'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif}
.dvz-mask{position:fixed;inset:0;z-index:99998;background:transparent}
.dvz-filter{width:100%;box-sizing:border-box;background:#22242a;border:none;border-bottom:1px solid #3a3d45;color:#e8e8e8;padding:8px 10px;font-size:12.5px;outline:none}
.dvz-tree{max-height:280px;overflow:auto;padding:4px 0}
.dvz-trow{display:flex;align-items:center;gap:6px;padding:5px 10px;font-size:12.5px;color:#dcdcdc;cursor:pointer;white-space:nowrap;overflow:hidden}
.dvz-trow:hover{background:rgba(255,255,255,.07)}
.dvz-trow.dvz-cur{color:#4a9eff}
.dvz-dir{color:#c8cad0;font-weight:600}
.dvz-arrow{width:12px;flex-shrink:0;color:#8a8f99}
.dvz-empty{padding:10px;color:#8a8f99;font-size:12px;text-align:center}
.dvz-row .dvz-str{width:58px;flex-shrink:0;background:#22242a;color:#e8e8e8;border:1px solid #3a3d45;border-radius:6px;padding:4px;font-size:12px;outline:none}
.dvz-row .dvz-str:focus{border-color:#4a9eff}
.dvz-row .dvz-mode{width:26px;height:24px;flex-shrink:0;border-radius:6px;font-size:13px;cursor:pointer;user-select:none;line-height:1;padding:0}
.dvz-row .dvz-mode.dvz-std{background:transparent;border:1px solid #666;color:#9aa0aa}
.dvz-row .dvz-mode.dvz-bypass{background:#2d5a3e;border:1px solid #3f8f5f;color:#9fe8b8}
.dvz-row .dvz-note{flex:.5;min-width:0;background:transparent;color:#cfcfcf;border:none;border-bottom:1px dashed #3a3d45;padding:4px 2px;font-size:12px;outline:none}
.dvz-row .dvz-note:focus{border-bottom-color:#4a9eff}
.dvz-row .dvz-del{width:20px;height:20px;line-height:18px;text-align:center;border:none;border-radius:5px;background:transparent;color:#ff6b6b;font-size:15px;cursor:pointer;flex-shrink:0;padding:0}
.dvz-row .dvz-del:hover{background:rgba(255,107,107,.15)}
.dvz-bar{display:flex;gap:6px;align-items:center;width:100%;margin-top:2px}
.dvz-bar .dvz-add{flex:1;text-align:center;padding:6px 0;border:1px dashed #3a3d45;border-radius:8px;color:#9aa0aa;font-size:12.5px;cursor:pointer;user-select:none;background:rgba(255,255,255,.03)}
.dvz-bar .dvz-add:hover{border-color:#4a9eff;color:#4a9eff}
.dvz-bar .dvz-mv{width:34px;text-align:center;padding:6px 0;border:1px solid #3a3d45;border-radius:8px;color:#9aa0aa;font-size:12.5px;cursor:pointer;user-select:none;background:rgba(255,255,255,.03)}
.dvz-bar .dvz-mv:hover{border-color:#4a9eff;color:#4a9eff}
.dvz-bar .dvz-cnt{font-size:11px;color:#8a8f99;flex-shrink:0;min-width:34px;text-align:center}
.dvz-full{color:#e8a13c;font-size:11.5px;text-align:center;margin-top:4px}
.dvz-tip{font-size:11px;color:#8a8f99;text-align:center;margin-top:4px;line-height:1.3}
`;
  document.head.appendChild(s);
})();

// ── LoRA 文件清单（在线拉取，Windows 路径归一化）──────────────
let LORA_FILES = null;
let LORA_FILES_PROMISE = null;

async function fetchLoraFiles() {
  for (const url of ["/api/object_info/LoraLoaderModelOnly", "/object_info/LoraLoaderModelOnly", "/api/object_info/LoraLoader"]) {
    try {
      const r = await fetch(url);
      if (!r.ok) continue;
      const d = await r.json();
      const values =
        d?.LoraLoaderModelOnly?.input?.required?.lora_name?.[0] ??
        d?.LoraLoader?.input?.required?.lora_name?.[0];
      if (Array.isArray(values) && values.length) return values;
    } catch (e) { /* 下一条路由重试 */ }
  }
  return null;
}

function ensureLoraList(node) {
  if (!LORA_FILES_PROMISE) {
    LORA_FILES_PROMISE = fetchLoraFiles().then((v) => {
      if (v) {
        LORA_FILES = v;
        if (node) rebuildPanel(node);
      }
      return v;
    });
  }
  return LORA_FILES_PROMISE;
}

function getLoraList() {
  return Array.isArray(LORA_FILES) ? LORA_FILES : [];
}

const normPath = (p) => String(p).replace(/\\/g, "/");

// ── 树形选择弹窗（Filter 搜索 + 文件夹树，同原生加载器形态）────
let DVZ_POP = null;
let DVZ_MASK = null;
let DVZ_PLACE = null;
function closeTreePopup() {
  if (DVZ_POP) { DVZ_POP.remove(); DVZ_POP = null; }
  if (DVZ_MASK) { DVZ_MASK.remove(); DVZ_MASK = null; }
  if (DVZ_PLACE) { window.removeEventListener("resize", DVZ_PLACE); DVZ_PLACE = null; }
}

function openTreePopup(anchor, files, current, onPick, ev) {
  closeTreePopup();
  DVZ_MASK = document.createElement("div");
  DVZ_MASK.className = "dvz-mask";
  DVZ_MASK.addEventListener("mousedown", closeTreePopup);
  document.body.appendChild(DVZ_MASK);

  const pop = document.createElement("div");
  pop.className = "dvz-pop";
  const inp = document.createElement("input");
  inp.className = "dvz-filter";
  inp.placeholder = "Filter list";
  const tree = document.createElement("div");
  tree.className = "dvz-tree";
  pop.append(inp, tree);
  document.body.appendChild(pop);
  DVZ_POP = pop;

  const place = () => {
    const r = anchor.getBoundingClientRect();
    const W = Math.max(280, r.width || 280);
    pop.style.width = W + "px";
    let left = r.left;
    let top = r.bottom + 4;
    if ((!r.width && !r.height) && ev) { left = ev.clientX; top = ev.clientY + 4; }
    left = Math.max(8, Math.min(left, window.innerWidth - W - 8));
    if (top + pop.offsetHeight > window.innerHeight - 8) {
      top = Math.max(8, (r.top || (ev ? ev.clientY : 0)) - pop.offsetHeight - 4);
    }
    pop.style.left = left + "px";
    pop.style.top = top + "px";
  };
  place();
  requestAnimationFrame(place);
  window.addEventListener("resize", place);
  DVZ_PLACE = place;

  const expanded = new Set();
  if (current) {
    const parts = normPath(current).split("/");
    for (let i = 1; i < parts.length; i++) expanded.add(parts.slice(0, i).join("/"));
  }

  const addFileRow = (f, depth, showPath) => {
    const d = document.createElement("div");
    d.className = "dvz-trow" + (f === current ? " dvz-cur" : "");
    d.style.paddingLeft = 10 + depth * 14 + "px";
    d.textContent = showPath ? normPath(f) : normPath(f).split("/").pop();
    d.title = normPath(f);
    d.onclick = () => { closeTreePopup(); onPick(f); };
    tree.appendChild(d);
  };

  const render = () => {
    const kw = inp.value.trim().toLowerCase();
    tree.innerHTML = "";
    if (!files.length) {
      const e = document.createElement("div");
      e.className = "dvz-empty";
      e.textContent = "（未找到 LoRA 文件）";
      tree.appendChild(e);
      return;
    }
    if (kw) {
      const hits = files.filter((f) => normPath(f).toLowerCase().includes(kw));
      hits.forEach((f) => addFileRow(f, 0, true));
      if (!hits.length) {
        const e = document.createElement("div");
        e.className = "dvz-empty";
        e.textContent = "无匹配";
        tree.appendChild(e);
      }
      return;
    }
    const root = { dirs: new Map(), files: [] };
    for (const f of files) {
      const parts = normPath(f).split("/");
      let n = root;
      for (let i = 0; i < parts.length - 1; i++) {
        if (!n.dirs.has(parts[i])) n.dirs.set(parts[i], { dirs: new Map(), files: [] });
        n = n.dirs.get(parts[i]);
      }
      n.files.push(f);
    }
    const walk = (dirsMap, fileArr, depth, path) => {
      [...dirsMap.keys()].sort().forEach((name) => {
        const full = path ? path + "/" + name : name;
        const open = expanded.has(full);
        const d = document.createElement("div");
        d.className = "dvz-trow dvz-dir";
        d.style.paddingLeft = 10 + depth * 14 + "px";
        const arrow = document.createElement("span");
        arrow.className = "dvz-arrow";
        arrow.textContent = open ? "▾" : "▸";
        const label = document.createElement("span");
        label.textContent = "📁 " + name;
        d.append(arrow, label);
        d.onclick = () => {
          if (expanded.has(full)) expanded.delete(full); else expanded.add(full);
          render();
        };
        tree.appendChild(d);
        if (open) {
          const sub = dirsMap.get(name);
          walk(sub.dirs, sub.files, depth + 1, full);
        }
      });
      fileArr.forEach((f) => addFileRow(f, depth, false));
    };
    walk(root.dirs, root.files, 0, "");
  };

  inp.oninput = render;
  inp.onkeydown = (e) => { if (e.key === "Escape") closeTreePopup(); };
  render();
  inp.focus();
}

// ── 行数据 ────────────────────────────────────────────────────
function normMode(v) {
  return v === MODE_BYPASS ? MODE_BYPASS : MODE_STD;
}

function defaultRow() {
  const files = getLoraList();
  return { on: true, file: files.length ? files[0] : "", strength: 1.0, mode: MODE_STD, note: "" };
}

function syncConfig(node) {
  const cfg = node.widgets?.find((w) => w.name === "LoRA配置");
  const json = JSON.stringify(node._dvzRows || []);
  if (cfg) cfg.value = json;
}

// 从 LoRA配置 恢复面板行；兼容 v0 老行（无 mode 键 → 标准）
function restoreRows(node) {
  const cfg = node.widgets?.find((w) => w.name === "LoRA配置");
  let rows = [];
  try {
    rows = JSON.parse(cfg?.value ?? "[]");
  } catch (e) { /* 脏数据走默认 */ }
  if (!Array.isArray(rows)) rows = [];
  rows = rows
    .filter((x) => x && typeof x === "object")
    .map((x) => ({
      on: x.on !== false,
      file: typeof x.file === "string" ? x.file : "",
      strength: Number.isFinite(Number(x.strength)) ? Number(x.strength) : 1.0,
      mode: normMode(x.mode),
      note: typeof x.note === "string" ? x.note : "",
    }));
  node._dvzRows = rows.length ? rows : [defaultRow()];
}

// ── 行构建 ────────────────────────────────────────────────────
function applyModeBtn(btn, mode) {
  btn.classList.toggle("dvz-std", mode !== MODE_BYPASS);
  btn.classList.toggle("dvz-bypass", mode === MODE_BYPASS);
  btn.title = mode === MODE_BYPASS
    ? "旁路：前向叠加，不改权重（量化基座 bypass 变体用）。点击切回标准"
    : "标准：权重融合（LoRA only 变体/风格/修复类用）。点击切为旁路";
}

function buildRow(node, row, index) {
  const div = document.createElement("div");
  div.className = "dvz-row" + (row.on ? "" : " dvz-off") + (node._dvzSel === index ? " dvz-sel" : "");
  div.onclick = () => { node._dvzSel = index; markSelection(node); };

  const cb = document.createElement("input");
  cb.type = "checkbox";
  cb.checked = !!row.on;
  cb.title = "勾选启用 / 取消禁用该 LoRA";
  cb.onclick = (e) => e.stopPropagation();
  cb.onchange = () => {
    row.on = cb.checked;
    div.classList.toggle("dvz-off", !row.on);
    syncConfig(node);
  };

  const fileBtn = document.createElement("button");
  fileBtn.type = "button";
  fileBtn.className = "dvz-file";
  const setName = (v) => {
    fileBtn.textContent = v ? normPath(v).split("/").pop() : "（未选择 LoRA）";
    fileBtn.title = v || "未选择";
  };
  if (!row.file) {
    const files = getLoraList();
    if (files.length) row.file = files[0];
  }
  setName(row.file);
  fileBtn.onclick = (e) => {
    e.stopPropagation();
    openTreePopup(fileBtn, getLoraList(), row.file, (v) => {
      row.file = v;
      setName(v);
      syncConfig(node);
    }, e);
  };

  const str = document.createElement("input");
  str.className = "dvz-str";
  str.type = "number";
  str.step = "0.05";
  str.min = "0";
  str.max = "2";
  str.value = row.strength ?? 1;
  str.title = "模型强度 (0 ~ 2)";
  str.onclick = (e) => e.stopPropagation();
  const onStr = () => {
    const v = parseFloat(str.value);
    if (!isNaN(v)) {
      row.strength = Math.max(0, Math.min(2, v));
      syncConfig(node);
    }
  };
  str.oninput = onStr;
  str.onchange = onStr;

  const mode = document.createElement("button");
  mode.type = "button";
  mode.className = "dvz-mode";
  mode.textContent = "⇄";
  applyModeBtn(mode, row.mode);
  mode.onclick = (e) => {
    e.stopPropagation();
    row.mode = row.mode === MODE_BYPASS ? MODE_STD : MODE_BYPASS;
    applyModeBtn(mode, row.mode);
    syncConfig(node);
  };

  const note = document.createElement("input");
  note.className = "dvz-note";
  note.type = "text";
  note.placeholder = "备注";
  note.title = "该 LoRA 的备注";
  note.value = row.note || "";
  note.onclick = (e) => e.stopPropagation();
  note.oninput = () => { row.note = note.value; syncConfig(node); };

  const del = document.createElement("button");
  del.className = "dvz-del";
  del.textContent = "×";
  del.title = "删除该行";
  del.onclick = (e) => {
    e.stopPropagation();
    node._dvzRows.splice(index, 1);
    if ((node._dvzSel ?? -1) >= node._dvzRows.length) node._dvzSel = node._dvzRows.length - 1;
    rebuildPanel(node);
    syncConfig(node);
  };

  div.append(cb, fileBtn, str, mode, note, del);
  return div;
}

function markSelection(node) {
  const wrap = node._dvzWrap;
  if (!wrap) return;
  [...wrap.querySelectorAll(".dvz-row")].forEach((el, i) => {
    el.classList.toggle("dvz-sel", i === (node._dvzSel ?? -1));
  });
}

// ── 面板重建 ──────────────────────────────────────────────────
function rebuildPanel(node) {
  const wrap = node._dvzWrap;
  if (!wrap) return;
  wrap.innerHTML = "";

  const rows = node._dvzRows;
  rows.forEach((row, i) => wrap.appendChild(buildRow(node, row, i)));

  const bar = document.createElement("div");
  bar.className = "dvz-bar";
  const add = document.createElement("div");
  add.className = "dvz-add";
  add.textContent = "＋ 添加 LoRA";
  add.onclick = (e) => {
    e.stopPropagation();
    node._dvzRows.push(defaultRow());
    node._dvzSel = node._dvzRows.length - 1;
    rebuildPanel(node);
    syncConfig(node);
  };
  const up = document.createElement("div");
  up.className = "dvz-mv";
  up.textContent = "↑";
  up.title = "选中行上移";
  up.onclick = (e) => { e.stopPropagation(); moveRow(node, -1); };
  const down = document.createElement("div");
  down.className = "dvz-mv";
  down.textContent = "↓";
  down.title = "选中行下移";
  down.onclick = (e) => { e.stopPropagation(); moveRow(node, +1); };
  const cnt = document.createElement("div");
  cnt.className = "dvz-cnt";
  cnt.textContent = `${rows.length}/${MAX_ROWS}`;
  bar.append(add, up, down, cnt);
  wrap.appendChild(bar);

  if (rows.length >= MAX_ROWS) {
    const full = document.createElement("div");
    full.className = "dvz-full";
    full.textContent = `最多 ${MAX_ROWS} 个 LoRA，先删除再添加`;
    wrap.appendChild(full);
  }
  const tip = document.createElement("div");
  tip.className = "dvz-tip";
  tip.textContent = "⇄ 灰=标准(权重融合) · 绿=旁路(量化基座变体)；点行选中后可 ↑↓ 排序";
  wrap.appendChild(tip);

  markSelection(node);

  // 高度：行 ~40px + 工具条/提示 ~78px；同步 canvas(computeSize) 与 Vue 布局层(get*Height)
  node._dvzHeight = rows.length * 40 + 78;
  if (node._dvzDomW) {
    node._dvzDomW.computeSize = () => [node.size[0], node._dvzHeight];
  }
  resizeNode(node);
}

function moveRow(node, dir) {
  const rows = node._dvzRows;
  if (!rows.length) return;
  const sel = Math.max(0, Math.min(rows.length - 1, Number(node._dvzSel ?? rows.length - 1)));
  const tgt = sel + dir;
  if (tgt < 0 || tgt >= rows.length) return;
  const t = rows[sel];
  rows[sel] = rows[tgt];
  rows[tgt] = t;
  node._dvzSel = tgt;
  rebuildPanel(node);
  syncConfig(node);
}

// ── 节点尺寸（只增不减守卫）──────────────────────────────────
function resizeNode(node) {
  try {
    const cs = node.computeSize();
    const minH = (node._dvzHeight ?? 60) + 80;
    node.setSize([
      Math.max(cs[0], node.size?.[0] ?? 0),
      Math.max(cs[1], minH),
    ]);
    node.setDirtyCanvas(true, true);
  } catch (e) { /* 布局层早于画布就绪时忽略 */ }
}

// ── 面板初始化 ────────────────────────────────────────────────
function setupPanel(node) {
  // 隐藏兜底 widget（LoRA文件/模型强度：扩展未加载时仍可单 LoRA 用）；LoRA配置=面板 JSON 载体
  for (const name of ["LoRA文件", "模型强度", "LoRA配置"]) {
    const w = node.widgets?.find((x) => x.name === name);
    if (!w) continue;
    w.hidden = true;
    w.computeSize = () => [0, 0];
    w.draw = () => {};
    w.mouse = () => {};
  }
  // 序列化兜底：提交 prompt / 保存工作流时都吐最新面板 JSON
  const cfg = node.widgets?.find((w) => w.name === "LoRA配置");
  if (cfg) {
    cfg.serializeValue = () => JSON.stringify(node._dvzRows || []);
  }

  restoreRows(node);
  node._dvzHeight = 90;
  node._dvzSel = node._dvzRows.length - 1;

  const wrap = document.createElement("div");
  wrap.className = "dvz-wrap";
  wrap.addEventListener("mousedown", (e) => e.stopPropagation());
  wrap.addEventListener("keydown", (e) => e.stopPropagation());

  node._dvzWrap = wrap;
  node._dvzDomW = node.addDOMWidget("lora面板", "lora面板", wrap, {
    serialize: false,
    getMinHeight: () => node._dvzHeight,
    getMaxHeight: () => node._dvzHeight,
    getHeight: () => node._dvzHeight,
  });

  rebuildPanel(node);
  syncConfig(node);
  node.size[0] = Math.max(node.size[0], 360);
  resizeNode(node);
  ensureLoraList(node);
}

// ── 注册扩展 ──────────────────────────────────────────────────
app.registerExtension({
  name: "deciia.vramsafe.lora.stack",

  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== NODE_NAME) return;

    const onCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = onCreated ? onCreated.apply(this, arguments) : undefined;
      setupPanel(this);
      return r;
    };

    // 加载已保存工作流：从 LoRA配置 JSON 恢复面板
    const onConfigure = nodeType.prototype.configure;
    nodeType.prototype.configure = function (info) {
      const r = onConfigure ? onConfigure.apply(this, arguments) : undefined;
      try {
        restoreRows(this);
        if (!this._dvzSel) this._dvzSel = this._dvzRows.length - 1;
        rebuildPanel(this);
        syncConfig(this);
        ensureLoraList(this);
      } catch (e) {
        console.warn("[Deciia VramSafe LoRA] configure:", e);
      }
      return r;
    };
  },
});

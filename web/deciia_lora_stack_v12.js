/**
 * Deciia 动态多 LoRA 串 v12（作者标记 deciia）
 * v12（2026-09-12 变更）：**删除「方式列 显示/隐藏」功能**——方式列常显，不再有隐藏态，
 *   随之删掉整条隐藏/重排链路（applyWayVisibility / onToggleWay / 隐藏按钮）。
 *   目标收敛为一句话：每个槽（启用/LoRA/强度/方式）四件套永远齐全，方式（标准|旁路）随
 *   prompt 一起发到后端，LoRA 正常挂载。槽配置仍以 properties.deciia_config 为权威。
 * v11（2026-09-12）：① 加载路径也走 refreshNodeSoon（v10 只在切显隐/增删槽时延后重排，
 *   加载时前端异步尺寸回写会把节点高度盖回旧值 → 实测加载后 7 槽节点 771px(应 606)、
 *   9 槽 939px(应 750)，底部留白=方式列占的行高；② ensureWayWidgets 补进所有加载路径
 *   （configure 重建 / 双 RAF / reassert / onNodeCreated）——旧档或异常路径下漏建的 方式_i
 *   控件会在加载时立即补全，不再等点按钮。
 * v10（2026-09-12）：修「切方式列只多一条空白行 / 其它槽看不到方式下拉」。
 *   真因：显隐切换后没重排 —— 前端 widget.y / widget.computedHeight 仍是旧值，节点高度也与
 *   computeSize() 脱节（实测 390(应)/458/441(实际)），恢复出来的行落在节点体外被裁掉。
 *   修：refreshNode 改为 arrange()（前端排版入口，内部 getLayoutWidgets 已扣 hidden）
 *   + setSize(高度对齐 computeSize()) + 画布置脏。
 * v9（2026-09-12）：方式列显隐改走前端官方开关 widget.hidden。
 *   v6~v8 用 computeSize/draw 猴补丁压行高：这个前端（1.51.x）的排版认
 *   `isWidgetVisible()`/`getLayoutWidgets()`（都看 widget.hidden），猴补丁
 *   反而把行高算坏 —— 隐藏后节点反而变高，且「恢复显示」因为原值记的是 undefined 而永远回不去。
 *   现在：隐藏 = widget.hidden=true + options.hidden=true（值照旧进 prompt / widgets_values），
 *   显示 = 置回 false。
 * v8（2026-09-12）：真正修掉「加载后第 1 个 LoRA 被清空/整体错位」。
 *   v7 只归一化 widgets_values，不够 —— 前端 LGraphNode.configure 的位置灌值是
 *   `for (w of this.widgets) if (w.serialize !== false) w.value = wv[t++]`，
 *   它跳过按钮条(widget) 但 **不会跳过数组里的前导 null**（那是 model 输入占位，
 *   当前节点没有对应控件）→ 从第 1 槽起恒差一格：启用_1=null、LoRA_1=true、
 *   强度_1=LoRA 名 → 后端判名非法回落 none（=第 1 个 LoRA 被清空）。
 *   修法：configure 包装里先把槽配置固定到 properties（properties 优先 → named 值
 *   → 位置数组解析），然后 **把 widgets_values 清空**（位置路径空转，怎么灌都灌不坏），
 *   基类 configure 返回后同步重建一次槽控件。
 * v7（2026-09-11 深夜）：修「加载工作流后第 1 个 LoRA 被清空/整体错位」。
 *   根因：基类 LGraphNode.configure() 按**位置**把 info.widgets_values 灌进 node.widgets
 *   （for i: this.widgets[i].value = wv[i]）。老档每槽存 3 值，v6 每槽有 4 个控件
 *   （启用/LoRA/强度/方式）→ 从第 1 槽起整体错位一格：启用_1=null、LoRA_1=true、
 *   强度_1=LoRA 名、方式_1=0.93 → 后端判 LoRA 名非法 → 回落 none（=第 1 个 LoRA 被清空）。
 *   修：包装 prototype.configure，在基类灌值前把 widgets_values 归一化成「表头 + 每槽 4 值」，
 *   方式 缺省取 properties.deciia_config（权威）。另：syncConfig 改为「控件值非法则保留 properties 旧值」，
 *   避免任何错位/占位值把配置写坏。
 * v5（2026-09-11）：① 补 dom.serialize = false（v4 只传了 options.serialize，
 *   导致按钮条以 "" 混进工作流 widgets_values → 加载按索引回填错位）
 *   ② 后端已改 V3 + accept_all_inputs，槽位值终于能到后端，本 JS 无需配合改动。
 * v6（2026-09-11）：方式列改「隐藏但保留控件」。v5 在 deciia_way_hidden 时压根不创建
 *   方式_i 控件 → API prompt 里没有 方式_i → 后端 kwargs.get("方式_i","标准") 全回落
 *   标准，旁路槽形同虚设（实测 prompt 只有 启用/LoRA/强度）。现在：
 *   方式_i 永远创建（保证进 prompt 与 widgets_values），隐藏=computeSize 归零+DOM
 *   display:none（不占行、不可点），显示=还原；只有删槽才真删控件。
 *   另：onConfigure 兜底解析兼容 3 值/槽(旧档)与 4 值/槽(v6)，并剥掉表头 null 与尾部 ""。
 * 按钮条 = DOM widget, KJNodes 同款同步挂载 + 每次绘制自愈:
 *   - onNodeCreated 同步 ensureButtons（不进 RAF/onConfigure 异步）
 *   - onDraw 每帧检查 bar 是否在 DOM 树 / 是否可见, 脱落自动重挂
 *   - onConfigure 只同步值, 不动结构（增删槽仍由按钮驱动）
 * 槽数/配置权威源 = node.properties.deciia_slots / deciia_config（随 JSON 持久化）
 * v12 起：方式列常显（隐藏功能已删，properties.deciia_way_hidden 不再被读取）
 */
import { app } from "../../../scripts/app.js";

const NODE_NAME = "DeciiaLoraStack";
// 方式字面量：前后端共用，禁止翻译/改写（后端 mode not in ("标准","旁路") → 回落标准）
const WAY_STD = "标准";
const WAY_BYPASS = "旁路";
const WAY_VALUES = [WAY_STD, WAY_BYPASS];
const MAX_SLOTS = 16;
const BTN_WIDGET = "deciia_lora_btns";

function findW(node, name) {
  return node.widgets?.find((w) => w && w.name === name);
}
function allBtnWidgets(node) {
  return (node.widgets || []).filter((w) => w && w.name === BTN_WIDGET);
}
function getSlotCount(node) {
  if (typeof node._deciiaSlotCount === "number") {
    return Math.max(0, Math.min(MAX_SLOTS, node._deciiaSlotCount));
  }
  const n = Number(node.properties?.deciia_slots);
  return Math.max(0, Math.min(MAX_SLOTS, Number.isFinite(n) ? n : 1));
}
function setSlotCount(node, n) {
  n = Math.max(0, Math.min(MAX_SLOTS, Number(n) || 0));
  node._deciiaSlotCount = n;
  if (!node.properties) node.properties = {};
  node.properties.deciia_slots = n;
  return n;
}
function refreshNode(node) {
  // v10: 增删槽/切显隐后必须「重排 + 对齐高度」，否则：
  //   - widget.y / computedHeight 还是旧值 → 新增或恢复的行被画到节点体外被裁掉（表现为只多一条空白行）
  //   - node.size 与 computeSize() 脱节（实测 390(应) vs 458/441(实际)）
  // arrange() 是前端的排版入口（内部用 getLayoutWidgets()，已扣除 widget.hidden）；
  // computeSize() 同样已把 hidden 算掉，所以高度直接对齐它。
  try { node.arrange?.(); } catch (_) {}
  try {
    if (typeof node.computeSize === "function") {
      const sz = node.computeSize();
      if (Array.isArray(sz) && sz.length >= 2) {
        node.setSize([Math.max(node.size?.[0] || 0, sz[0]), sz[1]]);
      }
    }
  } catch (_) {}
  try { node.setDirtyCanvas?.(true, true); } catch (_) {}
  try { node.graph?.setDirtyCanvas?.(true, true); } catch (_) {}
}
function collapseWidget(w) {
  if (!w) return;
  w.type = "hidden";
  w.computeSize = () => [0, -4];
  try { w.hidden = true; } catch (_) {}
  try { w.draw = function () {}; } catch (_) {}
}
function destroyWidget(node, w) {
  if (!w) return;
  collapseWidget(w);
  try { w.element?.remove?.(); } catch (_) {}
  try { w.options?.element?.remove?.(); } catch (_) {}
  const idx = node.widgets?.indexOf(w);
  if (idx >= 0) node.widgets.splice(idx, 1);
}

/** 值同步: widgets → properties.deciia_config */
function syncConfig(node) {
  const cfg = [];
  const n = getSlotCount(node);
  const prev = Array.isArray(node.properties?.deciia_config) ? node.properties.deciia_config : [];
  for (let i = 1; i <= n; i++) {
    const en = findW(node, `启用_${i}`);
    const lo = findW(node, `LoRA_${i}`);
    const st = findW(node, `强度_${i}`);
    const md = findW(node, `方式_${i}`);
    const p = prev[i - 1] || {};
    const enV = en ? en.value : undefined;
    const loV = lo ? lo.value : undefined;
    const stV = st ? Number(st.value) : NaN;
    const mdV = md ? String(md.value) : "";
    // 清单没加载完时 combo 只有占位项，value 会被强成 none —— 此时不采信控件值
    const listReady = !lo || !Array.isArray(lo.options?.values) || lo.options.values.length > 1;
    cfg.push({
      // v7: 控件值不合法(错位灌值/占位初建)时保留 properties 旧值，绝不把配置写坏
      启用: typeof enV === "boolean" ? enV : (p.启用 ?? true),
      LoRA: (listReady && typeof loV === "string" && loV) ? loV : (p.LoRA ?? "none"),
      强度: Number.isFinite(stV) ? stV : (Number.isFinite(Number(p.强度)) ? Number(p.强度) : 1.0),
      方式: (mdV === WAY_STD || mdV === WAY_BYPASS) ? mdV : (p.方式 === WAY_BYPASS ? WAY_BYPASS : WAY_STD),
    });
  }
  if (!node.properties) node.properties = {};
  node.properties.deciia_slots = cfg.length;
  node.properties.deciia_config = cfg;
  return cfg;
}


/** 兜底：槽存在但没有 方式_i 控件时补建（旧档/异常路径），值取 properties.deciia_config */
function ensureWayWidgets(node) {
  const n = getSlotCount(node);
  const cfg = Array.isArray(node.properties?.deciia_config) ? node.properties.deciia_config : [];
  for (let i = 1; i <= n; i++) {
    if (findW(node, `方式_${i}`)) continue;
    if (!findW(node, `LoRA_${i}`)) continue; // 槽本身不存在
    const val = cfg[i - 1]?.方式 === WAY_BYPASS ? WAY_BYPASS : WAY_STD;
    node.addWidget("combo", `方式_${i}`, val, () => syncConfig(node), { values: WAY_VALUES.slice() });
  }
}

function _normWay(v) {
  return v === WAY_BYPASS ? WAY_BYPASS : WAY_STD;
}
function _normSlot(raw, i, stride) {
  return {
    启用: !!raw[i],
    LoRA: (typeof raw[i + 1] === "string" && raw[i + 1]) ? raw[i + 1] : "none",
    强度: Number.isFinite(Number(raw[i + 2])) ? Number(raw[i + 2]) : 1.0,
    方式: stride === 4 ? _normWay(raw[i + 3]) : WAY_STD,
  };
}

/** 从任意来源解析出权威槽配置：properties → named 值 → 位置数组（自动判 3/4 值布局）。 */
function slotConfigFromInfo(info) {
  const props = info?.properties?.deciia_config;
  if (Array.isArray(props) && props.length) {
    return props.map((c) => ({
      启用: c?.启用 !== false,
      LoRA: (typeof c?.LoRA === "string" && c.LoRA) ? c.LoRA : "none",
      强度: Number.isFinite(Number(c?.强度)) ? Number(c.强度) : 1.0,
      方式: _normWay(c?.方式),
    }));
  }
  // named 值：按名字取，天然不怕错位
  const named = info?.widgets_values_named;
  if (named && typeof named === "object" && (("LoRA_1" in named) || ("启用_1" in named))) {
    const out = [];
    for (let i = 1; i <= MAX_SLOTS; i++) {
      if (!(`LoRA_${i}` in named) && !(`启用_${i}` in named) && !(`强度_${i}` in named)) break;
      out.push({
        启用: typeof named[`启用_${i}`] === "boolean" ? named[`启用_${i}`] : true,
        LoRA: (typeof named[`LoRA_${i}`] === "string" && named[`LoRA_${i}`]) ? named[`LoRA_${i}`] : "none",
        强度: Number.isFinite(Number(named[`强度_${i}`])) ? Number(named[`强度_${i}`]) : 1.0,
        方式: _normWay(named[`方式_${i}`]),
      });
    }
    if (out.length) return out;
  }
  // 位置数组：剥前导 null（model 占位）与尾部 ""，再判步长
  const raw = Array.isArray(info?.widgets_values) ? info.widgets_values.slice() : [];
  while (raw.length && (raw[raw.length - 1] === "" || raw[raw.length - 1] === null || raw[raw.length - 1] === undefined)) raw.pop();
  while (raw.length && (raw[0] === null || raw[0] === undefined)) raw.shift();
  const is4 = raw.length >= 4 && (raw[3] === WAY_STD || raw[3] === WAY_BYPASS);
  const stride = is4 ? 4 : 3;
  const out = [];
  for (let i = 0; i + stride - 1 < raw.length; i += stride) out.push(_normSlot(raw, i, stride));
  return out;
}

/** 为槽 i 添加四件套控件 */
function addSlotWidgets(node, i, provided = {}) {
  // 清单优先级: 节点缓存 > 全局缓存(app._deciiaLoraList) > ["none"] — 防加载时红边
  const list = node._deciiaLoraList?.length ? node._deciiaLoraList
    : (window._deciiaGlobalLoraList?.length ? window._deciiaGlobalLoraList : ["none"]);
  node.addWidget("toggle", `启用_${i}`, provided.启用 ?? true, () => syncConfig(node), {});
  node.addWidget("combo", `LoRA_${i}`, provided.LoRA ?? "none", () => syncConfig(node), { values: list.slice() });
  node.addWidget("number", `强度_${i}`, provided.强度 ?? 1.0, () => syncConfig(node), { precision: 2, step: 0.05, min: -100, max: 100 });
  // v12: 方式列常显、永远创建——值必须进 prompt（后端按 方式_i 决定标准/旁路加载）
  const wayVal = provided.方式 === WAY_BYPASS ? WAY_BYPASS : WAY_STD;
  node.addWidget("combo", `方式_${i}`, wayVal, () => syncConfig(node), { values: WAY_VALUES.slice() });
}

/** 移除槽 i 的四件套控件 */
function removeSlotWidgets(node, i) {
  for (const k of [`启用_${i}`, `LoRA_${i}`, `强度_${i}`, `方式_${i}`]) {
    const w = findW(node, k);
    if (!w) continue;
    try { w.element?.remove?.(); } catch (_) {}
    const idx = node.widgets.indexOf(w);
    if (idx >= 0) node.widgets.splice(idx, 1);
  }
}

function applySlotCount(node, count) {
  const cur = getSlotCount(node);
  const n = setSlotCount(node, count);
  if (n > cur) for (let i = cur + 1; i <= n; i++) addSlotWidgets(node, i);
  else if (n < cur) for (let i = cur; i > n; i--) removeSlotWidgets(node, i);
  syncConfig(node);
  updateButtonState(node);
  refreshNodeSoon(node);
}

function onAddSlot(node) {
  console.log("[Deciia LoRA] 添加槽触发, 节点:", node.id);
  const cur = getSlotCount(node);
  if (cur >= MAX_SLOTS) return;
  applySlotCount(node, cur + 1);
  refreshNode(node);
}
function onDelSlot(node) {
  const cur = getSlotCount(node);
  if (cur <= 0) return;
  removeSlotWidgets(node, cur);
  applySlotCount(node, cur - 1);
  refreshNode(node);
}
function swapSlots(node, a, b) {
  if (a < 1 || b < 1 || a > MAX_SLOTS || b > MAX_SLOTS || a === b) return;
  for (const k of ["启用", "LoRA", "强度", "方式"]) {
    const wa = findW(node, `${k}_${a}`);
    const wb = findW(node, `${k}_${b}`);
    if (!wa || !wb) continue;
    const t = wa.value; wa.value = wb.value; wb.value = t;
  }
  syncConfig(node);
  refreshNode(node);
}
function onMove(node, dir) {
  const cur = getSlotCount(node);
  if (cur <= 1) return;
  const sel = Math.max(1, Math.min(cur, Number(node._deciiaSlotSel) || cur));
  const tgt = sel + dir;
  if (tgt < 1 || tgt > cur) return;
  swapSlots(node, sel, tgt);
  node._deciiaSlotSel = tgt;
}

/** DOM 按钮条: 只建一次; 脱落自愈在 onDraw */
function ensureButtons(node) {
  if (node._deciiaLoraBtns?.bar?.isConnected) {
    // 已在 DOM 树: 每次确保贴底
    const kept = allBtnWidgets(node)[0];
    if (kept) {
      const idx = node.widgets.indexOf(kept);
      if (idx >= 0 && idx !== node.widgets.length - 1) {
        node.widgets.splice(idx, 1);
        node.widgets.push(kept);
      }
    }
    return node._deciiaLoraBtns;
  }
  // 旧 widget 清理
  for (const w of allBtnWidgets(node)) destroyWidget(node, w);
  node._deciiaLoraBtns = null;

  const bar = document.createElement("div");
  bar.className = "deciia-lora-btns";
  bar.dataset.deciiaLoraBar = "1";
  bar.style.cssText = "display:flex;flex-direction:column;gap:6px;width:100%;box-sizing:border-box;padding:4px 2px 2px;pointer-events:auto;";

  const mkBtn = (text, bg, title) => {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = text;
    b.title = title;
    b.style.cssText = `flex:1;cursor:pointer;border:1px solid #666;border-radius:4px;padding:6px 4px;font-size:12px;color:#eee;background:${bg};pointer-events:auto;user-select:none;`;
    return b;
  };

  const row = document.createElement("div");
  row.style.cssText = "display:flex;gap:6px;width:100%;";
  const addBtn = mkBtn("＋ 添加槽", "#2d4a3e", "添加一个 LoRA 槽位");
  const delBtn = mkBtn("－ 删除末槽", "#4a2d2d", "删除最后一个槽位");
  const upBtn = mkBtn("↑ 上移", "#2d3a4a", "选中槽上移（默认末槽）");
  const downBtn = mkBtn("↓ 下移", "#2d3a4a", "选中槽下移（默认末槽）");
  row.append(addBtn, delBtn, upBtn, downBtn);

  // v12: 方式列按钮已删除（方式列常显）——第二行只保留选中槽提示
  const row2 = document.createElement("div");
  row2.style.cssText = "display:flex;gap:6px;width:100%;";
  const selInfo = document.createElement("div");
  selInfo.style.cssText = "flex:1;display:flex;align-items:center;justify-content:center;font-size:11px;color:#aaa;";
  selInfo.textContent = "选中槽: 末槽";
  row2.append(selInfo);

  const info = document.createElement("div");
  info.style.cssText = "font-size:11px;color:#aaa;text-align:center;line-height:1.2;";
  info.textContent = `槽位 ${getSlotCount(node)} / ${MAX_SLOTS}`;

  bar.append(row, row2, info);

  const bind = (el, fn) => {
    const h = (e) => { e.preventDefault(); e.stopPropagation(); try { fn(); } catch (err) { console.warn("[Deciia LoRA]", err); } return false; };
    el.addEventListener("pointerdown", h, true);
  };
  bind(addBtn, () => onAddSlot(node));
  bind(delBtn, () => onDelSlot(node));
  bind(upBtn, () => onMove(node, -1));
  bind(downBtn, () => onMove(node, +1));

  const dom = node.addDOMWidget(BTN_WIDGET, "div", bar, { serialize: false });
  // 注意区分两个开关（frontend src/lib/litegraph/src/LGraphNode.ts 与 utils/executionUtil.ts）：
  //   widget.options.serialize=false → 只排除 prompt
  //   widget.serialize=false         → 排除工作流磁盘序列化（widgets_values）
  // 只传 options 会让按钮条以 "" 混进 widgets_values，造成加载时按索引回填错位（09-11 修）
  try { dom.serialize = false; } catch (_) {}
  dom.computeSize = () => [node.size?.[0] || 300, 64];

  node._deciiaLoraBtns = { bar, dom, info, selInfo };
  updateButtonState(node);
  return node._deciiaLoraBtns;
}

function updateButtonState(node) {
  const pack = node._deciiaLoraBtns;
  if (!pack?.info) return;
  try { pack.info.textContent = `槽位 ${getSlotCount(node)} / ${MAX_SLOTS}`; } catch (_) {}
}

/** 重建全部槽控件(值从 properties.deciia_config) — 方式列显隐切换时用 */
function rebuildSlots(node) {
  const cfg = syncConfig(node);
  for (let i = 1; i <= getSlotCount(node); i++) removeSlotWidgets(node, i);
  const n = getSlotCount(node);
  for (let i = 1; i <= n; i++) addSlotWidgets(node, i, cfg[i - 1] || {});
}

/** v10: 前端会在下一帧异步回写节点尺寸（实测慢一步），所以重排要"立刻 + 延后再来两次"。 */
function refreshNodeSoon(node) {
  refreshNode(node);
  const again = () => { try { refreshNode(node); } catch (_) {} };
  try { requestAnimationFrame(() => requestAnimationFrame(again)); } catch (_) { setTimeout(again, 32); }
  setTimeout(again, 260);
}

/* ============================================================
   扩展注册
   ============================================================ */
app.registerExtension({
  name: "Deciia.LoraStack",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== NODE_NAME) return;

    const fetchLoraList = async () => {
      try {
        const res = await fetch("/object_info/LoraLoaderModelOnly");
        if (res.ok) {
          const d = await res.json();
          const vals = d?.LoraLoaderModelOnly?.input?.required?.lora_name?.[0];
          if (Array.isArray(vals) && vals.length) {
            const full = ["none", ...vals.filter((v) => v !== "none")];
            window._deciiaGlobalLoraList = full;
            return full;
          }
        }
      } catch (_) {}
      return null;
    };
    app._deciiaLoraListPromise = app._deciiaLoraListPromise || (async () => {
      // fetch 失败(null)不提前结束, 重试一次; 再失败用超时兜底 — 保证 resolve 一定是清单
      let vals = await fetchLoraList();
      if (!vals) { await new Promise((r) => setTimeout(r, 1500)); vals = await fetchLoraList(); }
      return vals || window._deciiaGlobalLoraList || ["none"];
    })();

    nodeType.prototype.onNodeCreated = function () {
      try {
        this._deciiaSlotCount = Number(this.properties?.deciia_slots);
        if (!Number.isFinite(this._deciiaSlotCount)) this._deciiaSlotCount = 1;
        // 同步挂 DOM 按钮条(KJ 同款, 不进异步)
        ensureButtons(this);
        // v6: 新建节点(无 deciia_slots)时把 1 号槽真建出来 —— 旧版计数器写 1/16 但一行控件都没有，
        // 且首次「添加槽」会直接跳到 2 号槽，槽 1 永远不存在。清单未到时先用占位清单，靠红框自愈回填。
        try {
          const nAtStart = getSlotCount(this);
          const cfg0 = Array.isArray(this.properties?.deciia_config) ? this.properties.deciia_config : [];
          for (let i = 1; i <= nAtStart; i++) {
            if (!findW(this, `启用_${i}`)) addSlotWidgets(this, i, cfg0[i - 1] || {});
          }
          ensureWayWidgets(this); // v11: 漏建的方式控件立刻补全
        } catch (e0) {
          console.warn("[Deciia LoRA] ensureSlot1:", e0);
        }
        app._deciiaLoraListPromise?.then((vals) => {
          if (!vals || !this.widgets) return;
          this._deciiaLoraList = vals;
          const cfg = this.properties?.deciia_config;
          const n = getSlotCount(this);
          for (let i = 1; i <= n; i++) {
            const w = findW(this, `LoRA_${i}`);
            if (!w || !w.options) continue;
            w.options.values = vals.slice();
            const m = String(w.name).match(/_(\d+)$/);
            const c = cfg?.[Number(m?.[1]) - 1];
            if (c?.LoRA && vals.includes(c.LoRA)) w.value = c.LoRA;
            else if (!vals.includes(w.value)) w.value = "none";
          }
          refreshNodeSoon(this);
        });
      } catch (e) {
        console.warn("[Deciia LoRA] onNodeCreated:", e);
      }
      return undefined;
    };

    // v8: 基类 configure 的「位置灌值」对动态槽控件天然错位（前导 null 无人消费）。
    // 做法：先把配置固定进 properties，再清空位置数组让那条路空转，最后同步重建槽控件。
    const configure = nodeType.prototype.configure;
    nodeType.prototype.configure = function (info) {
      let cfg = null;
      try {
        if (info && typeof info === "object") {
          cfg = slotConfigFromInfo(info);
          if (cfg && cfg.length) {
            if (!info.properties) info.properties = {};
            info.properties.deciia_slots = cfg.length;
            info.properties.deciia_config = cfg;
          }
          // 位置/named 两条灌值路径全部断掉：值只认 properties
          if (Array.isArray(info.widgets_values)) info.widgets_values = [];
          if (info.widgets_values_named) info.widgets_values_named = {};
        }
      } catch (e) {
        console.warn("[Deciia LoRA] configure 归一化失败:", e);
      }
      const r = configure ? configure.apply(this, arguments) : undefined;
      // 基类 configure 之后立刻按 properties 重建一次（双 RAF 那次仍保留，用于清单晚到场景）
      try {
        if (cfg && cfg.length) {
          if (!this.properties) this.properties = {};
          this.properties.deciia_slots = cfg.length;
          this.properties.deciia_config = cfg;
          this._deciiaSlotCount = cfg.length;
          // 注意：不能走 rebuildSlots（它先 syncConfig，会拿控件的默认值回写 properties，
          // 把 强度/方式 冲成默认）——必须用权威 cfg 直接重建
          for (let i = 1; i <= cfg.length; i++) removeSlotWidgets(this, i);
          for (let i = 1; i <= cfg.length; i++) addSlotWidgets(this, i, cfg[i - 1]);
          ensureWayWidgets(this); // v11: 旧档/异常路径漏建的方式控件立即补全
          // 此处**不能** syncConfig：LoRA 清单可能还没到，combo 会把非法名强成 none，
          // 一回写就把权威配置冲成 none。配置已在上方写入 properties，等清单自愈即可。
        }
      } catch (e) {
        console.warn("[Deciia LoRA] configure 重建失败:", e);
      }
      // 加载后自愈：任何一条加载路径（位置灌值/草稿回灌/前端重建）把控件冲成默认，
      // 都在稍后按 properties.deciia_config 重新对齐一次 —— 配置是权威，控件只是展示。
      const reassert = () => {
        try {
          const cfg2 = Array.isArray(this.properties?.deciia_config) ? this.properties.deciia_config : [];
          for (let i = 1; i <= cfg2.length; i++) {
            const c = cfg2[i - 1] || {};
            const en = findW(this, `启用_${i}`), lo = findW(this, `LoRA_${i}`);
            const st = findW(this, `强度_${i}`), md = findW(this, `方式_${i}`);
            if (en && typeof c.启用 === "boolean") en.value = c.启用;
            if (st && Number.isFinite(Number(c.强度))) st.value = Number(c.强度);
            if (md) md.value = _normWay(c.方式);
            const vals = lo?.options?.values;
            if (lo && c.LoRA && Array.isArray(vals) && vals.length > 1 && vals.includes(c.LoRA) && lo.value !== c.LoRA) {
              lo.value = c.LoRA;
            }
          }
          ensureWayWidgets(this); // v11: reassert 时也补全漏建的方式控件
          refreshNodeSoon(this);
        } catch (e) {
          console.warn("[Deciia LoRA] reassert:", e);
        }
      };
      setTimeout(reassert, 120);
      setTimeout(reassert, 900);
      return r;
    };

    // 工作流加载: properties.deciia_config 权威; widgets_values 仅兜底
    const onConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function (info) {
      const r = onConfigure ? onConfigure.apply(this, arguments) : undefined;
      try {
        let slots = null;
        const cfg = info?.properties?.deciia_config;
        if (Array.isArray(cfg) && cfg.length) {
          slots = cfg;
        } else {
          const wv = info?.widgets_values;
          if (Array.isArray(wv)) {
            // 剥掉表头 null/undefined（model 输入位）与尾部按钮条残留 ""
            const arr = wv.slice();
            while (arr.length && (arr[0] === null || arr[0] === undefined)) arr.shift();
            while (arr.length && (arr[arr.length - 1] === "" || arr[arr.length - 1] === null)) arr.pop();
            // 步长判定: 第 4 位是 标准/旁路 字符串 → 4 值/槽(v6 起)；否则 3 值/槽(旧档，方式按标准)
            const stride4 = arr.length >= 4 && (arr[3] === WAY_STD || arr[3] === WAY_BYPASS);
            const stride = stride4 ? 4 : 3;
            slots = [];
            for (let i = 0; i + stride - 1 < arr.length; i += stride) {
              slots.push({
                启用: !!arr[i],
                LoRA: typeof arr[i + 1] === "string" ? arr[i + 1] : "none",
                强度: Number.isFinite(Number(arr[i + 2])) ? Number(arr[i + 2]) : 1.0,
                方式: stride4 && arr[i + 3] === WAY_BYPASS ? WAY_BYPASS : WAY_STD,
              });
            }
          }
        }
        if (slots) {
          if (!this.properties) this.properties = {};
          this.properties.deciia_slots = slots.length;
          this.properties.deciia_config = slots;
        }
        this._deciiaSlotCount = Math.max(0, Math.min(MAX_SLOTS, slots ? slots.length : 1));
        // 同步重建: 按钮 + 槽(不进 RAF)
        ensureButtons(this);
        const n = getSlotCount(this);
        const finalCfg = this.properties.deciia_config || [];
        // v1/v2 时序(实证无红框): 旧槽同步清, 新槽延到双 RAF 后建 — 那时清单必已就绪
        for (let i = 1; i <= n; i++) removeSlotWidgets(this, i);
        requestAnimationFrame(() => requestAnimationFrame(() => {
          if (!this.widgets) return;
          const cfgR = this.properties?.deciia_config || [];
          const nn = getSlotCount(this);
          for (let i = 1; i <= nn; i++) { removeSlotWidgets(this, i); addSlotWidgets(this, i, cfgR[i - 1] || {}); }
          ensureWayWidgets(this); // v11: 双 RAF 重建后同样补全
          refreshNodeSoon(this);
        }));
        app._deciiaLoraListPromise?.then((vals) => {
          if (!vals || !this.widgets) return;
          this._deciiaLoraList = vals;
          const cfg2 = this.properties?.deciia_config;
          for (let i = 1; i <= n; i++) {
            const w = findW(this, `LoRA_${i}`);
            if (!w || !w.options) continue;
            w.options.values = vals.slice();
            const c = cfg2?.[i - 1];
            if (c?.LoRA && vals.includes(c.LoRA)) w.value = c.LoRA;
            else if (!vals.includes(w.value)) w.value = "none";
          }
          refreshNodeSoon(this);
        });
        refreshNodeSoon(this);
      } catch (e) {
        console.warn("[Deciia LoRA] configure:", e);
      }
      return r;
    };

    // 自愈: 每次节点绘制检查按钮条 DOM 脱落
    const onDraw = nodeType.prototype.onDraw || nodeType.prototype.onDrawForeground;
    nodeType.prototype.onDraw = function (ctx) {
      if (onDraw) try { onDraw.apply(this, arguments); } catch (_) {}
      try {
        if (this._deciiaLoraBtns?.bar && !this._deciiaLoraBtns.bar.isConnected) {
          ensureButtons(this); // 脱落自愈: 重挂
        }
        // 红框自愈: combo 候选仍是占位清单而全局清单已到 → 填充+校值(每帧低频检查, 有 _flag 跳过)
        const g = window._deciiaGlobalLoraList;
        if (g && g.length > 1 && this.widgets && !this._deciiaListOk) {
          const cfg = this.properties?.deciia_config;
          const n = getSlotCount(this);
          let fixed = false;
          for (let i = 1; i <= n; i++) {
            const w = findW(this, `LoRA_${i}`);
            if (!w || !w.options) continue;
            if (!w.options.values || w.options.values.length !== g.length) {
              w.options.values = g.slice();
              fixed = true;
            }
            if (!w.options.values.includes(w.value)) {
              const c = cfg?.[i - 1];
              w.value = c?.LoRA && g.includes(c.LoRA) ? c.LoRA : "none";
              fixed = true;
            }
            // 清除 DOM 元素上的红框类(1.51 初建值无效时打的标记, 修值不会自动摘除)
            const el = w.element || w.inputEl;
            if (el?.classList?.length) {
              [...el.classList].filter((c2) => /invalid|error|warn/i.test(c2)).forEach((c2) => el.classList.remove(c2));
            }
          }
          if (!fixed) this._deciiaListOk = true; // 已一致, 停止逐帧检查
          else try { this.graph?.change?.(); } catch (_) {}
        }
      } catch (_) {}
    };
  },
});

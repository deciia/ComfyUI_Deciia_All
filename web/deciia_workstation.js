/**
 * Deciia 工作台 v1（作者 deciia）
 * ============================================================
 * ComfyUI 侧栏「工作台」启动器：工具条目完全自定义，不再固定三个入口。
 *
 * 入口：
 *   1) ComfyUI 左侧栏「工作台」标签（registerSidebarTab）
 *   2) 画布节点 DeciiaWorkstation 上的「打开工作台」按钮（浮层，参考曜石导演台）
 *
 * 打开模式（每条工具独立）：
 *   embed      → ComfyUI 内浮层 iframe（同曜石导演台体验）
 *   new_window → 浏览器新窗口/新标签
 *
 * 添加方式：
 *   手动填写 / 粘贴一行解析 / 粘贴 JSON 批量导入 / 扫描本机常见端口自动发现
 *
 * 数据：后端 /deciia_workstation/tools（user/default/deciia_workstation/tools.json），
 *       带 revision 冲突检测；所有用户输入一律 textContent 渲染，不拼 HTML。
 * ============================================================
 */
import { app } from "../../../scripts/app.js";

const NODE_NAME = "DeciiaWorkstation";
const API = "/deciia_workstation";
const COMMON_PORTS = [3000, 3001, 5173, 7860, 7861, 8000, 8080, 8188, 8189, 8888, 9119, 5000, 6006];

const state = {
  tools: [],
  revision: 0,
  loaded: false,
  loading: false,
  loadAttempted: false,
  error: "",
  scanning: false,
  editingId: null,
};

let overlay = null;

/* ------------------------------------------------------------------ 后端通信 */

async function api(path, options = {}) {
  const resp = await fetch(API + path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const text = await resp.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { /* 非 JSON 响应 */ }
  if (!resp.ok) {
    throw new Error((data && data.error) || `请求失败 HTTP ${resp.status}`);
  }
  return data;
}

async function loadTools(force = false) {
  if (state.loading) return;
  if (state.loaded && !force) return;
  if (state.loadAttempted && !force) return;   // 失败后不再自动重试，避免渲染死循环
  state.loadAttempted = true;
  state.loading = true;
  try {
    const data = await api("/tools");
    state.tools = data.tools || [];
    state.revision = data.revision || 0;
    state.loaded = true;
    state.error = "";
  } catch (error) {
    state.error = String(error.message || error);
  } finally {
    state.loading = false;
  }
}

async function persistTools(tools) {
  const data = await api("/tools", {
    method: "POST",
    body: JSON.stringify({ tools, expected_revision: state.revision }),
  });
  state.tools = data.tools || [];
  state.revision = data.revision || 0;
  state.loaded = true;
  state.error = "";
  return data;
}

async function probeTool(tool) {
  const url = tool.status_url || tool.url;
  const mode = tool.status_mode || "http";
  if (mode === "none") return null;
  return api("/probe", { method: "POST", body: JSON.stringify({ url, status_mode: mode }) });
}

async function mutateTools(mutator) {
  const draft = state.tools.map((t) => ({ ...t }));
  const result = mutator(draft);
  if (result === false) return;
  try {
    await persistTools(draft);
  } catch (error) {
    state.error = String(error.message || error);
  }
}

/* ------------------------------------------------------------------ 打开工具 */

function toolUrl(tool) {
  return String(tool.url || "");
}

function openTool(tool) {
  const url = toolUrl(tool);
  if (!url) return;
  if ((tool.open_mode || "embed") === "new_window") {
    window.open(url, "_blank", "noopener");
    return;
  }
  openOverlay(tool, url);
}

function closeOverlay() {
  if (!overlay) return;
  try { overlay.close(); } catch { /* 已关闭 */ }
  overlay.remove();
  overlay = null;
}

/** ComfyUI 内浮层（参考曜石导演台：顶栏 + 全宽 iframe + 返回画布）。 */
function openOverlay(tool, url) {
  closeOverlay();
  const dialog = document.createElement("dialog");
  dialog.style.cssText =
    "position:fixed;inset:1vh 1vw;width:98vw;height:98vh;max-width:none;max-height:none;" +
    "padding:0;border:1px solid color-mix(in srgb,currentColor 22%,transparent);border-radius:14px;" +
    "background:var(--comfy-menu-bg,#101314);color:inherit;z-index:10000";

  const bar = document.createElement("div");
  bar.style.cssText =
    "height:44px;display:flex;align-items:center;gap:10px;justify-content:space-between;padding:0 14px;font-size:13px";

  const label = document.createElement("span");
  label.textContent = `${tool.icon || "🧩"} ${tool.name} · 加载中…`;
  label.style.cssText = "flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap";

  const actions = document.createElement("div");
  actions.style.cssText = "display:flex;gap:8px;flex-shrink:0";
  const external = document.createElement("button");
  external.textContent = "在新窗口打开";
  external.title = "若下方内容空白（目标站点禁止内嵌），用此按钮改为新窗口";
  external.onclick = () => window.open(url, "_blank", "noopener");
  const close = document.createElement("button");
  close.textContent = "返回画布";
  close.onclick = () => closeOverlay();
  actions.append(external, close);
  bar.append(label, actions);

  const frame = document.createElement("iframe");
  frame.title = tool.name;
  frame.src = url;
  frame.style.cssText = "width:100%;height:calc(100% - 44px);border:0;display:block;background:transparent";
  frame.addEventListener("load", () => { label.textContent = `${tool.icon || "🧩"} ${tool.name}`; });
  frame.addEventListener("error", () => {
    label.textContent = `${tool.name} · 内嵌失败，请改用「在新窗口打开」`;
  });

  dialog.append(bar, frame);
  document.body.append(dialog);
  dialog.showModal();
  overlay = dialog;
}

/* ------------------------------------------------------------------ 小工具 */

function el(tag, styles, text) {
  const node = document.createElement(tag);
  if (styles) node.style.cssText = styles;
  if (text !== undefined) node.textContent = text;
  return node;
}

function btn(label, styles, onClick, title = "") {
  const node = el("button", styles, label);
  node.type = "button";
  if (title) node.title = title;
  node.addEventListener("click", onClick);
  return node;
}

function parsePastedLine(line) {
  const raw = String(line || "").trim();
  if (!raw) return null;
  const parts = raw.split("|").map((s) => s.trim());
  if (parts.length === 1) {
    const url = parts[0];
    let name = url;
    try {
      const parsed = new URL(url, location.origin);
      name = parsed.port ? `${parsed.hostname}:${parsed.port}` : parsed.hostname;
    } catch { /* 保持原文 */ }
    return { name, url, icon: "🧩", hint: "", open_mode: "new_window" };
  }
  return {
    name: parts[0] || "未命名工具",
    url: parts[1] || "",
    icon: parts[2] || "🧩",
    hint: parts[3] || "",
    open_mode: parts[4] === "embed" ? "embed" : "new_window",
  };
}

function shortHost(url) {
  try {
    const parsed = new URL(url, location.origin);
    return parsed.port ? `${parsed.hostname}:${parsed.port}` : parsed.hostname;
  } catch {
    return url;
  }
}

/* ------------------------------------------------------------------ 面板渲染 */

const CARD_STYLE =
  "display:grid;gap:14px;padding:16px;border-radius:12px;" +
  "border:1px solid color-mix(in srgb,currentColor 18%,transparent);" +
  "background:color-mix(in srgb,currentColor 5%,transparent)";
const ROW_STYLE = "display:flex;align-items:center;gap:8px";
const OPEN_BTN_STYLE =
  "width:100%;padding:10px 12px;border:1px solid #7d6848;border-radius:9px;" +
  "background:#2a241c;color:#f0cf98;font:600 13px/1.2 inherit;cursor:pointer";
const MINI_BTN_STYLE =
  "padding:4px 8px;border:1px solid color-mix(in srgb,currentColor 20%,transparent);" +
  "border-radius:7px;background:transparent;color:inherit;font:500 11px/1.2 inherit;cursor:pointer";
const TITLE_STYLE = "margin:0 0 6px;font-size:16px;line-height:1.3;font-weight:600";
const SUMMARY_STYLE = "margin:0;opacity:.72;font-size:12px;line-height:1.6";
const HINT_STYLE = "margin:0;opacity:.58;font-size:11px;line-height:1.55";

function renderPanel(host) {
  host.replaceChildren();
  host.style.cssText = "height:100%;overflow:auto;box-sizing:border-box;padding:14px;color:inherit;font-family:inherit";
  host.dataset.deciiaWorkstation = "true";

  if (!state.loaded && !state.loading) loadTools().then(() => renderPanel(host));

  const wrap = el("div", "display:grid;gap:10px;align-content:start");

  /* 标题 */
  const head = el("div", "display:grid;gap:4px");
  head.append(
    el("h2", "margin:0;font-size:16px;line-height:1.3", "工作台"),
    el("p", "margin:0;opacity:.68;font-size:12px;line-height:1.6",
       "统一入口打开本机工具与服务。条目可自行增删，每条可选择在 ComfyUI 内浮层打开或新窗口打开。"),
  );
  wrap.append(head);

  /* 工具条 */
  const toolbar = el("div", "display:flex;flex-wrap:wrap;gap:6px");
  toolbar.append(
    btn("＋ 添加工具", MINI_BTN_STYLE, () => { state.editingId = "__new__"; renderPanel(host); }),
    btn("📥 批量导入", MINI_BTN_STYLE, () => { state.editingId = "__import__"; renderPanel(host); }),
    btn(state.scanning ? "扫描中…" : "🔍 扫描本机服务", MINI_BTN_STYLE, async () => {
      state.scanning = true;
      renderPanel(host);
      await scanLocalServices();
      state.scanning = false;
      renderPanel(host);
    }),
    btn("↻ 刷新", MINI_BTN_STYLE, () => { state.loadAttempted = false; state.loaded = false;
      return loadTools(true).then(() => renderPanel(host)); }),
  );
  wrap.append(toolbar);

  if (state.error) {
    wrap.append(el("p", "margin:0;padding:8px 10px;border-radius:8px;font-size:12px;" +
      "background:color-mix(in srgb,#e5534b 18%,transparent);color:#ffb4ae", state.error));
  }
  if (state.loading && !state.loaded) {
    wrap.append(el("p", "margin:0;opacity:.6;font-size:12px", "读取工具清单…"));
  }

  /* 编辑器（新增 / 导入） */
  if (state.editingId === "__new__") wrap.append(buildEditor(null, host));
  if (state.editingId === "__import__") wrap.append(buildImportPanel(host));

  /* 工具卡片 */
  const visible = state.tools.filter((t) => t.enabled !== false);
  if (state.loaded && !visible.length) {
    wrap.append(el("p", "margin:0;opacity:.6;font-size:12px", "还没有工具，点「＋ 添加工具」或「🔍 扫描本机服务」。"));
  }
  for (const tool of visible) {
    wrap.append(state.editingId === tool.id ? buildEditor(tool, host) : buildCard(tool, host));
  }

  host.append(wrap);
  queueStatusProbes(host);
}

function buildCard(tool, host) {
  const card = el("section", CARD_STYLE);

  /* 标题行：图标 + 名称 + 右侧小标签 */
  const head = el("div", "");
  const row = el("div", ROW_STYLE);
  row.append(
    el("span", "font-size:18px;line-height:1;flex-shrink:0", tool.icon || "🧩"),
    el("h2", TITLE_STYLE + ";flex:1;margin:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap", tool.name),
    el("span", "font-size:11px;opacity:.55;flex-shrink:0",
       tool.hint || (tool.url && tool.url.startsWith("/") ? "站内" : shortHost(tool.url))),
  );
  head.append(row);
  /* 描述行 */
  if (tool.summary) head.append(el("p", SUMMARY_STYLE + ";margin:6px 0 0", tool.summary));
  card.append(head);

  /* 状态行（配置了探测才显示） */
  if ((tool.status_mode || "http") !== "none" && (tool.status_url || tool.url)) {
    const statusRow = el("div", ROW_STYLE);
    const dot = el("span", "width:8px;height:8px;border-radius:50%;background:#888;flex-shrink:0");
    dot.dataset.statusDot = tool.id;
    const text = el("span", HINT_STYLE + ";flex:1", "查询中…");
    text.dataset.statusText = tool.id;
    statusRow.append(dot, text);
    card.append(statusRow);
  }

  /* 全宽主按钮（曜石导演台同款琥珀色） */
  const embed = (tool.open_mode || "embed") !== "new_window";
  card.append(btn(embed ? "打开" : "在新窗口打开", OPEN_BTN_STYLE, () => openTool(tool), tool.url));

  /* 提示行 + 管理按钮 */
  const foot = el("div", "display:flex;align-items:center;gap:8px");
  foot.append(
    el("p", HINT_STYLE + ";flex:1;margin:0",
       embed ? "在 ComfyUI 内浮层打开，可用「返回画布」关闭。" : "在新浏览器窗口打开。"),
    btn("✎ 编辑", MINI_BTN_STYLE, () => { state.editingId = tool.id; renderPanel(host); }),
    btn("🗑 删除", MINI_BTN_STYLE, () => {
      if (!window.confirm(`删除工具「${tool.name}」？`)) return;
      mutateTools((draft) => {
        const idx = draft.findIndex((t) => t.id === tool.id);
        if (idx >= 0) draft.splice(idx, 1);
      }).then(() => renderPanel(host));
    }),
  );
  card.append(foot);
  return card;
}

function lockedInput(label, value, styles = "") {
  const box = el("label", "display:grid;gap:4px;font-size:11px;opacity:.85");
  box.append(el("span", "", label));
  const input = el("input", "padding:6px 8px;border-radius:7px;border:1px solid color-mix(in srgb,currentColor 22%,transparent);" +
    "background:color-mix(in srgb,currentColor 6%,transparent);color:inherit;font:500 12px/1.3 inherit;" + styles);
  input.value = value || "";
  box.append(input);
  return { box, input };
}

function buildEditor(tool, host) {
  const isNew = !tool;
  const draft = tool ? { ...tool } : {
    id: "", name: "", url: "", icon: "🧩", summary: "", hint: "",
    open_mode: "embed", status_url: "", status_mode: "http", status_label: "", enabled: true,
  };

  const card = el("section", CARD_STYLE + ";border-color:color-mix(in srgb,currentColor 32%,transparent)");
  card.append(el("h3", "margin:0;font-size:13px", isNew ? "添加工具" : `编辑：${tool.name}`));

  const nameF = lockedInput("名称", draft.name);
  const urlF = lockedInput("地址（站内路径 /xxx 或 http(s)://host:port/）", draft.url);
  const iconF = lockedInput("图标（emoji）", draft.icon, ";width:64px");
  const sumF = lockedInput("描述（一句话，可空）", draft.summary);
  const hintF = lockedInput("右侧小字（端口/标签，可空）", draft.hint);

  const modeRow = el("div", "display:grid;gap:4px;font-size:11px;opacity:.85");
  modeRow.append(el("span", "", "打开方式"));
  const modeSel = el("select", "padding:6px 8px;border-radius:7px;border:1px solid color-mix(in srgb,currentColor 22%,transparent);" +
    "background:color-mix(in srgb,currentColor 6%,transparent);color:inherit;font:500 12px/1.3 inherit");
  for (const [value, text] of [["embed", "ComfyUI 内浮层（同曜石导演台）"], ["new_window", "浏览器新窗口"]]) {
    const opt = el("option", "", text);
    opt.value = value;
    if ((draft.open_mode || "embed") === value) opt.selected = true;
    modeSel.append(opt);
  }
  modeRow.append(modeSel);

  const statusF = lockedInput("探测地址（可空，默认用上面的地址）", draft.status_url);
  const statusModeRow = el("div", "display:grid;gap:4px;font-size:11px;opacity:.85");
  statusModeRow.append(el("span", "", "探测方式"));
  const statusSel = el("select", "padding:6px 8px;border-radius:7px;border:1px solid color-mix(in srgb,currentColor 22%,transparent);" +
    "background:color-mix(in srgb,currentColor 6%,transparent);color:inherit;font:500 12px/1.3 inherit");
  for (const [value, text] of [["http", "HTTP 200 即在线"], ["json_field:backend", "JSON 字段（如 backend=ready）"], ["none", "不探测"]]) {
    const opt = el("option", "", text);
    opt.value = value;
    if ((draft.status_mode || "http") === value) opt.selected = true;
    statusSel.append(opt);
  }
  statusModeRow.append(statusSel);

  const actions = el("div", "display:flex;gap:6px;justify-content:flex-end");
  actions.append(
    btn("取消", MINI_BTN_STYLE, () => { state.editingId = null; renderPanel(host); }),
    btn("保存", MINI_BTN_STYLE + ";font-weight:700", () => {
      const entry = {
        id: draft.id,
        name: nameF.input.value.trim(),
        url: urlF.input.value.trim(),
        icon: iconF.input.value.trim() || "🧩",
        summary: sumF.input.value.trim(),
        hint: hintF.input.value.trim(),
        open_mode: modeSel.value,
        status_url: statusF.input.value.trim(),
        status_mode: statusSel.value,
        status_label: draft.status_label || "",
        enabled: true,
      };
      mutateTools((list) => {
        const idx = draft.id ? list.findIndex((t) => t.id === draft.id) : -1;
        if (idx >= 0) list[idx] = { ...list[idx], ...entry };
        else list.push(entry);
      }).then(() => { state.editingId = null; renderPanel(host); });
    }),
  );

  card.append(nameF.box, urlF.box, iconF.box, sumF.box, hintF.box, modeRow, statusF.box, statusModeRow, actions);
  return card;
}

function buildImportPanel(host) {
  const card = el("section", CARD_STYLE + ";border-color:color-mix(in srgb,currentColor 32%,transparent)");
  card.append(el("h3", "margin:0;font-size:13px", "批量导入"));
  card.append(el("p", "margin:0;font-size:11px;opacity:.66;line-height:1.6",
    "每行一条：名称 | 地址 | 图标(可空) | 备注(可空) | embed|new_window(可空)。只填地址也可以。"));

  const textarea = el("textarea", "width:100%;min-height:110px;padding:8px;border-radius:8px;box-sizing:border-box;" +
    "border:1px solid color-mix(in srgb,currentColor 22%,transparent);" +
    "background:color-mix(in srgb,currentColor 6%,transparent);color:inherit;font:500 12px/1.5 ui-monospace,monospace");
  textarea.placeholder = "NEXUS BTA Studio | http://127.0.0.1:7861/ | 🤖 | :7861\ndreamifly | http://127.0.0.1:3000/ | 🎨";

  const actions = el("div", "display:flex;gap:6px;justify-content:flex-end");
  actions.append(
    btn("取消", MINI_BTN_STYLE, () => { state.editingId = null; renderPanel(host); }),
    btn("直接导入", MINI_BTN_STYLE, () => {
      const lines = textarea.value.split("\n").map(parsePastedLine).filter(Boolean);
      if (!lines.length) return;
      mutateTools((list) => { for (const item of lines) list.push(item); })
        .then(() => { state.editingId = null; renderPanel(host); });
    }),
    btn("预览后逐个确认", MINI_BTN_STYLE + ";font-weight:700", () => {
      const lines = textarea.value.split("\n").map(parsePastedLine).filter(Boolean);
      if (!lines.length) return;
      // 预览：把解析结果写入剪贴板便于核对，同时进入新增编辑器逐条添加
      const preview = lines.map((l) => `${l.name} → ${l.url}`).join("\n");
      try { navigator.clipboard?.writeText(preview); } catch { /* 忽略 */ }
      window.alert(`解析出 ${lines.length} 条（已复制到剪贴板便于核对）：\n\n${preview}\n\n确认后逐条添加。`);
    }),
  );

  // JSON 导入
  const jsonBox = el("details", "font-size:11px;opacity:.9");
  jsonBox.append(el("summary", "从 JSON 导入"));
  const jsonArea = el("textarea", "width:100%;min-height:90px;margin-top:6px;padding:8px;border-radius:8px;box-sizing:border-box;" +
    "border:1px solid color-mix(in srgb,currentColor 22%,transparent);" +
    "background:color-mix(in srgb,currentColor 6%,transparent);color:inherit;font:500 12px/1.5 ui-monospace,monospace");
  jsonArea.placeholder = '[{"name":"工具A","url":"http://127.0.0.1:8080/","icon":"🧩","open_mode":"new_window"}]';
  const jsonBtn = btn("导入 JSON", MINI_BTN_STYLE + ";margin-top:6px", () => {
    let parsed;
    try { parsed = JSON.parse(jsonArea.value); } catch (error) {
      window.alert(`JSON 解析失败：${error.message}`);
      return;
    }
    const list = Array.isArray(parsed) ? parsed : parsed.tools;
    if (!Array.isArray(list) || !list.length) { window.alert("JSON 里没有工具数组"); return; }
    mutateTools((draft) => {
      for (const item of list) {
        draft.push({
          id: item.id || "",
          name: item.name || "未命名工具",
          url: item.url || "",
          icon: item.icon || "🧩",
          summary: item.summary || "",
          hint: item.hint || "",
          open_mode: item.open_mode === "new_window" ? "new_window" : "embed",
          status_url: item.status_url || "",
          status_mode: item.status_mode || "http",
          status_label: item.status_label || "",
          enabled: item.enabled !== false,
        });
      }
    }).then(() => { state.editingId = null; renderPanel(host); });
  });
  jsonBox.append(jsonArea, jsonBtn);

  card.append(textarea, actions, jsonBox);
  return card;
}

/* ------------------------------------------------------------------ 探测与扫描 */

function queueStatusProbes(host) {
  const cards = state.tools.filter((t) => t.enabled !== false);
  for (const tool of cards) {
    if ((tool.status_mode || "http") === "none") continue;
    if (!(tool.status_url || tool.url)) continue;
    probeTool(tool).then((result) => {
      if (!result) return;
      const dot = host.querySelector(`[data-status-dot="${tool.id}"]`);
      const text = host.querySelector(`[data-status-text="${tool.id}"]`);
      if (!dot || !text) return;
      const label = tool.status_label ? `${tool.status_label}：` : "";
      dot.style.background = result.ok ? "#3fb950" : "#e5534b";
      text.textContent = `${label}${result.ok ? "运行中" : "未就绪"} · ${result.detail || ""}`;
      text.style.opacity = result.ok ? ".72" : "1";
      if (!result.ok) text.style.color = "#ffb4ae";
    }).catch(() => { /* 探测失败不影响面板 */ });
  }
}

async function scanLocalServices() {
  const results = await Promise.all(COMMON_PORTS.map(async (port) => {
    try {
      const data = await api("/probe", {
        method: "POST",
        body: JSON.stringify({ url: `http://127.0.0.1:${port}/`, status_mode: "http" }),
      });
      return data.ok ? { port, title: "" } : null;
    } catch { return null; }
  }));
  const found = results.filter(Boolean);
  if (!found.length) { window.alert("本机常见端口未发现可访问服务。"); return; }

  const existing = new Set(state.tools.map((t) => t.url));
  const fresh = found.filter((f) => !existing.has(`http://127.0.0.1:${f.port}/`));
  if (!fresh.length) { window.alert(`发现 ${found.length} 个服务，但清单里都已存在。`); return; }
  const preview = fresh.map((f) => `127.0.0.1:${f.port}`).join("\n");
  if (!window.confirm(`发现 ${fresh.length} 个新服务：\n\n${preview}\n\n加入工作台？`)) return;
  await mutateTools((list) => {
    for (const item of fresh) {
      list.push({
        id: "", name: `本机服务 :${item.port}`, url: `http://127.0.0.1:${item.port}/`,
        icon: "🔌", summary: `本机 ${item.port} 端口服务`,
        hint: `:${item.port}`, open_mode: "embed",
        status_url: "", status_mode: "http", status_label: "", enabled: true,
      });
    }
  });
}

/* ------------------------------------------------------------------ 注册 */

app.registerExtension({
  name: "Deciia.Workstation",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== NODE_NAME) return;
    const onCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      onCreated?.apply(this, arguments);
      this.addWidget("button", "打开工作台", null, () => {
        closeOverlay();
        const dialog = document.createElement("dialog");
        dialog.style.cssText =
          "position:fixed;inset:2vh 8vw;width:84vw;height:96vh;max-width:none;max-height:none;padding:0;" +
          "border:1px solid color-mix(in srgb,currentColor 22%,transparent);border-radius:14px;" +
          "background:var(--comfy-menu-bg,#101314);color:inherit;z-index:10000";
        const bar = el("div", "height:44px;display:flex;align-items:center;justify-content:space-between;padding:0 14px;font-size:13px");
        bar.append(el("span", "", "工作台"), btn("返回画布", MINI_BTN_STYLE, () => {
          try { dialog.close(); } catch { /* 已关闭 */ }
          dialog.remove();
          overlay = null;
        }));
        const body = el("div", "height:calc(100% - 44px);overflow:auto");
        dialog.append(bar, body);
        document.body.append(dialog);
        dialog.showModal();
        overlay = dialog;
        loadTools().then(() => renderPanel(body));
      }, { serialize: false });
      this.setSize?.([380, 160]);
    };
  },
  async setup() {
    const manager = app.extensionManager;
    if (!manager?.registerSidebarTab) {
      console.warn("[Deciia Workstation] 当前 ComfyUI 不支持独立侧栏，请用节点上的「打开工作台」按钮。");
      return;
    }
    manager.registerSidebarTab({
      id: "deciia-workstation",
      icon: "pi pi-th-large",
      title: "工作台",
      tooltip: "Deciia 工作台（工具启动器）",
      type: "custom",
      render: (element) => {
        renderPanel(element);           // 立即出框架（含"读取工具清单…"）
        loadTools().then(() => renderPanel(element));
      },
    });
  },
});

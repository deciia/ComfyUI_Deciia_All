# -*- coding: utf-8 -*-
"""
Deciia 显存安全 LoRA 串 v1（deciia 自建包 ComfyUI_Deciia_LoraStacks，作者标记 deciia）
================================================================================
2026-09-15 变更：用本节点替换存量工作流里的旧节点
（6× BanZhang_LoRA_VramSafe + 3× DeciiaLoraStack）。

2026-09-26 变更：旧节点 DeciiaLoraStack 正式下线，其内核
_apply_model_lora / _is_dirty_lora_name 已完整迁入本文件（stacks/lora_stack.py 随之删除）。
未迁移：旧节点的动态槽控件（accept_all_inputs）与下拉候选表 _lora_list()——那是被本节点
JSON 面板取代的旧 UX，也是历史上位置灌值错位的根源；内核里已含脏名/不存在/强度0/不兼容的全部跳过路径。

= 班长面板 UX（banzhang_lora_loader v2.1）+ Deciia 旁路内核（deciia_lora_stack v12）合并 =
- 前端行式面板：每行 [启用] [LoRA文件(Filter+文件夹树弹层)] [强度] [⇄方式] [备注] [×]
- ⇄ 两态图标：灰空心=标准（权重融合） / 绿实心=旁路（前向叠加不改权重）
- 底部 [＋ 添加 LoRA] [↑ 上移] [↓ 下移]（点行选中，最多 16 行）
- 面板序列化为 JSON 经「LoRA配置」传入；「LoRA文件」+「模型强度」单 LoRA 兜底
- 后端：clone-first + 原生 patch / bypass + 不兼容自动跳过 + 显存体检日志
- 配置载体为单个 JSON widget（非动态槽控件），无 v12 位置灌值错位问题；
  prompt 提交由前端 serializeValue 兜底，位置错位免疫。

【2026-09-26 面板升级】方式列由「⇄ 两态按钮」改为「点击展开列表，四态可扩展」：
  标准（默认）/ 旁路 / 风格 / 人物；选中后按钮只显示图标，悬浮出文字说明（保持节点宽度）。
  语义分工：标准·风格·人物 都走权重融合挂载，差别只在输出给下游的 type 标签（风格=可被下游屏蔽）；
  旁路 走前向叠加不改权重。新增类型只改 MODE_TABLE（后端）+ DVZ_MODES（前端）两张表。
  输出 2 路：模型（挂载后, slot0） / LoRA配置（KREA_LORA_STACK 载荷, slot1）。
  底模 出口已于同日撤掉：需要干净底模时直接从上游加载节点接（功能等价, 同一个对象）；
  仅当本串输入是级联/合并来的模型时才会有差异（那时上游挂的 LoRA 会一并带进底模）。
  兼容：输出槽顺序保持 模型 在 slot0，存量连线不受影响。

【方式判定（2026-09-15 修正，重要）】旁路按「变体」判定，不按「加速类」一刀切：
同一加速 LoRA（如 LightX2V FL2V Turbo）的发布变体里，
  quantized-model bypass 变体（配量化/convrot 基座）→ 必须旁路（前向叠加不改量化权重）；
  LoRA only 变体（配 bf16 基座）→ 标准权重融合。
工作流迁移时逐文件对号，以各工作流现有
LoraLoaderBypassModelOnly / LoraLoaderModelOnly 链路为权威证据。
================================================================================
"""
from __future__ import annotations

import json
import logging

import comfy.model_management
import comfy.sd
import comfy.utils
import folder_paths
from comfy_api.latest import io

log = logging.getLogger(__name__)


# ── 以下两个函数 2026-09-26 自 stacks/lora_stack.py 原样迁入（旧节点下线，本文件成为内核唯一持有者）──
# 契约：_apply_model_lora 在所有跳过路径上都「原样返回传入对象」，_apply_row 依赖这一点判定是否生效。

def _is_dirty_lora_name(name: str) -> bool:
    """控件错位产生的假 LoRA 名（纯数字/布尔），不能拿去加载。"""
    n = (name or "").strip()
    if not n or n in ("none", "None"):
        return False
    if n in ("true", "false", "True", "False"):
        return True
    try:
        float(n)
        return True
    except Exception:
        return False

def _apply_model_lora(model, lora_name: str, strength: float, mode: str = "标准"):
    """挂模型 LoRA；mode=旁路 用核心 bypass 机制（前向叠加，不改权重）。不兼容跳过不中断。"""
    if model is None:
        return None
    if not lora_name or str(lora_name) in ("none", "None", ""):
        return model
    if _is_dirty_lora_name(str(lora_name)):
        log.warning("[Deciia LoRA] 非法名「%s」，跳过", lora_name)
        return model
    strength = float(strength)
    if abs(strength) < 1e-8:
        return model
    path = folder_paths.get_full_path("loras", str(lora_name))
    if not path:
        log.warning("[Deciia LoRA] 找不到: %s", lora_name)
        return model

    # 先 clone 再改 model_options：不污染调用方的模型对象（避免同图多分支/并发竞态）
    try:
        base = model.clone()
    except Exception:
        base = model
    opts = getattr(base, "model_options", None)
    prev_reject = None
    had_key = False
    if isinstance(opts, dict):
        had_key = "reject_unloaded_lora_weights" in opts
        prev_reject = opts.get("reject_unloaded_lora_weights", None)
        opts["reject_unloaded_lora_weights"] = False

    try:
        lora = comfy.utils.load_torch_file(path, safe_load=True)
        if mode == "旁路":
            model_lora, _ = comfy.sd.load_bypass_lora_for_models(base, None, lora, strength, 0.0)
            log.info("[Deciia LoRA] 已挂(bypass) %s @ %.3f", lora_name, strength)
        else:
            model_lora, _ = comfy.sd.load_lora_for_models(base, None, lora, strength, 0.0)
            log.info("[Deciia LoRA] 已挂 %s @ %.3f", lora_name, strength)
        return model_lora
    except ValueError as e:
        msg = str(e)
        if "lora key not loaded" in msg or "NOT LOADED" in msg:
            log.warning(
                "[Deciia LoRA] 与当前模型不兼容，已跳过 %s | %s",
                lora_name,
                msg[:200],
            )
            return model
        log.warning("[Deciia LoRA] 加载失败 %s: %s", lora_name, e)
        return model
    except Exception as e:
        log.warning("[Deciia LoRA] 加载失败 %s: %s", lora_name, e)
        return model
    finally:
        if isinstance(opts, dict):
            if had_key:
                opts["reject_unloaded_lora_weights"] = prev_reject
            else:
                opts.pop("reject_unloaded_lora_weights", None)

MODE_STD = "标准"
MODE_BYPASS = "旁路"
MODE_STYLE = "风格"
MODE_PERSON = "人物"

# ── 方式注册表（单一扩展点）────────────────────────────────────────────────
# 加新类型：只在这里加一条 + 前端 DVZ_MODES 加一条，其余逻辑无需改动。
#   apply : 后端实际挂载策略（标准=权重融合 / 旁路=前向叠加不改权重）
#   emit  : 输出给下游「LoRA配置」(KREA_LORA_STACK) 的 type 标签（Lazy 系认得 默认/风格/人物）
#   desc  : 说明（前端表为准，这里供日志与文档）
MODE_TABLE = {
    MODE_STD:    {"apply": MODE_STD,    "emit": "默认", "desc": "权重融合（默认）"},
    MODE_BYPASS: {"apply": MODE_BYPASS, "emit": "默认", "desc": "前向叠加，不改权重（量化基座 bypass 变体）"},
    MODE_STYLE:  {"apply": MODE_STD,    "emit": MODE_STYLE,  "desc": "选工作台风格时由下游自动屏蔽"},
    MODE_PERSON: {"apply": MODE_STD,    "emit": MODE_PERSON, "desc": "始终保留（不被风格屏蔽）"},
}

# 前后端共用字面量；未知值一律回落标准（软失败，不中断出片）
_MODE_ALIASES = {
    MODE_STD: MODE_STD, "std": MODE_STD, "standard": MODE_STD, "normal": MODE_STD, "默认": MODE_STD,
    MODE_BYPASS: MODE_BYPASS, "bypass": MODE_BYPASS,
    MODE_STYLE: MODE_STYLE, "style": MODE_STYLE,
    MODE_PERSON: MODE_PERSON, "person": MODE_PERSON, "character": MODE_PERSON,
}


def _gb(n) -> str:
    try:
        return f"{n / (1024 ** 3):.2f} GB"
    except Exception:
        return "?"


def _norm_mode(v) -> str:
    if v is None:
        return MODE_STD
    return _MODE_ALIASES.get(str(v).strip().lower(), MODE_STD)


def _rows_of(raw, group=None):
    """LoRA配置 载体 → 当前行列表（可选按组名选组）。

    支持两种格式（预设分组升级，2026-10-01）：
      旧: [行, 行, ...]                       —— v0/v1 面板 JSON，整包当"默认"组
      新: {"presets":[{name,rows},...], "current":"组名"} —— 预设分组版
    group 非空时优先取该名组（外部信号「模型选择」驱动）；取不到回落 current/第一组。
    """
    try:
        obj = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return []
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        presets = obj.get("presets")
        if isinstance(presets, list) and presets:
            want = str(group or "").strip()
            picked = None
            if want:
                picked = next((g for g in presets if isinstance(g, dict) and str(g.get("name")) == want), None)
            if picked is None:
                name = str(obj.get("current") or "").strip()
                picked = next((g for g in presets if isinstance(g, dict) and str(g.get("name")) == name), None)
            if picked is None:
                picked = presets[0]
            rows = picked.get("rows")
            return rows if isinstance(rows, list) else []
    return []


def _parse_rows(raw, group=None) -> list:
    """解析面板 JSON -> [(文件, 强度, 方式, 备注), ...]。未启用/未选文件的行跳过。"""
    rows = _rows_of(raw, group)
    if not isinstance(rows, list):
        rows = []

    jobs = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        if not r.get("on", True):
            continue
        file = str(r.get("file") or "").strip()
        if not file:
            continue
        try:
            strength = float(r.get("strength", 1.0))
        except (TypeError, ValueError):
            strength = 1.0
        strength = max(0.0, min(2.0, strength))
        jobs.append((file, strength, _norm_mode(r.get("mode")), str(r.get("note") or "").strip()))
    return jobs

def _emit_stack(raw, group=None) -> list:
    """面板行 → 下游认识的 KREA_LORA_STACK 载荷（Lazy 系格式：on/name/strength/type）。

    转换：file→name；mode（标准/旁路/风格/人物）→ type（默认/风格/人物）。
    旁路在载荷里表现为 默认 + 额外 mode 字段（下游认得出更好，认不出也无害）。
    """
    rows = _rows_of(raw, group)
    if not isinstance(rows, list):
        rows = []
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        mode = _norm_mode(r.get("mode"))
        try:
            strength = float(r.get("strength", 1.0))
        except (TypeError, ValueError):
            strength = 1.0
        out.append({
            "on": bool(r.get("on", True)),
            "name": str(r.get("file") or "").strip() or "none",
            "strength": max(0.0, min(2.0, strength)),
            "type": MODE_TABLE[mode]["emit"],
            "mode": mode,
        })
    return out


def _apply_row(model, file: str, strength: float, mode: str):
    """挂单行 LoRA。返回 (model, 状态描述)。内核同 v12 串：clone-first + patch/bypass + 不兼容跳过。"""
    path = folder_paths.get_full_path("loras", file)
    if path is None:
        return model, "❌ loras 目录下未找到（已跳过）"
    before = comfy.model_management.get_free_memory()
    out = _apply_model_lora(model, file, strength, MODE_TABLE.get(mode, MODE_TABLE[MODE_STD])["apply"])
    after = comfy.model_management.get_free_memory()
    if out is model:
        # 内核所有跳过路径都原样返回传入对象（强度0/非法名/找不到/不兼容/加载失败）
        return model, "⚠️ 未应用（强度0/非法名/不兼容/加载失败，已跳过）"
    tag = "旁路(前向叠加)" if mode == MODE_BYPASS else "标准(权重融合)"
    return out, f"✅ {tag}  显存 {_gb(before)}→{_gb(after)}"


class DeciiaVramSafeLoraStack(io.ComfyNode):
    """🛡️ Deciia 显存安全 LoRA 串。

    面板行数据（on/file/strength/mode/note）整体序列化进「LoRA配置」一个 STRING widget，
    由前端扩展维护；后端逐行按 标准/旁路 挂载，单行失败跳过不中断。
    """

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="DeciiaVramSafeLoraStack",
            display_name="🛡️ Deciia 显存安全 LoRA 串",
            category="Deciia/LoRA",
            description=(
                "显存安全多 LoRA 串：实时融合（原生 patch，不常驻满秩权重副本）。"
                "每行可选 标准（权重融合，LoRA only 变体/风格/修复类）或 "
                "旁路（前向叠加不改权重，量化基座 bypass 变体）。"
                "行式面板：启用/文件筛选/强度/⇄方式/备注/删除，可排序，最多 16 行。"
            ),
            inputs=[
                io.Model.Input("模型", tooltip="待挂 LoRA 的模型。"),
                io.String.Input(
                    "LoRA配置", default="[]", optional=True,
                    tooltip="面板 JSON（on/file/strength/mode/note），由前端扩展维护，勿手改。",
                ),
                io.String.Input(
                    "LoRA文件", default="", optional=True,
                    tooltip="兜底用：LoRA配置 为空时按此文件名加载单 LoRA（可含子目录路径）。",
                ),
                io.Float.Input(
                    "模型强度", default=1.0, min=0.0, max=2.0, step=0.01, optional=True,
                    tooltip="兜底用：LoRA配置 为空时该单 LoRA 的强度。",
                ),
                io.String.Input(
                    "模型选择", default="", optional=True, force_input=True,
                    tooltip="外部组信号（如 Anything Everywhere 广播的枢纽「当前组」）。"
                            "非空且与某预设组名一致时，执行用该组替代面板当前组。"
                            "（放最末位：forceInput 不占 widget 值槽，且避开旧值数组的剥析歧义）",
                ),
            ],
            outputs=[
                io.Model.Output(display_name="模型"),
                io.Custom("KREA_LORA_STACK").Output(
                    display_name="LoRA配置",
                    tooltip="面板行转成的 KREA_LORA_STACK 载荷（on/name/strength/type），"
                            "供下游按风格屏蔽类型=风格的槽（如 LazyMergeGenerate 的 LoRA配置 输入）。",
                ),
            ],
        )

    @classmethod
    def execute(cls, **kwargs) -> io.NodeOutput:
        model = kwargs.get("模型")
        if model is None:
            raise ValueError("[Deciia LoRA] 请连接 模型 输入。")

        sel = str(kwargs.get("模型选择") or "").strip()
        stack_out = _emit_stack(kwargs.get("LoRA配置"), sel)
        jobs = _parse_rows(kwargs.get("LoRA配置"), sel)
        if sel:
            print(f"🎛️ [Deciia显存安全LoRA] 外部模型选择信号: {sel}（命中预设组则用该组，否则回落面板当前组）")
        if not jobs:
            fallback_file = str(kwargs.get("LoRA文件") or "").strip()
            if fallback_file:
                try:
                    fb_strength = float(kwargs.get("模型强度", 1.0))
                except (TypeError, ValueError):
                    fb_strength = 1.0
                jobs = [(fallback_file, max(0.0, min(2.0, fb_strength)), MODE_STD, "")]

        if not jobs:
            print("⏸️ [Deciia显存安全LoRA] 未应用（无启用的 LoRA：取消勾选 / 未选文件 / 配置为空）")
            return io.NodeOutput(model, stack_out)

        free_before = comfy.model_management.get_free_memory()
        if free_before < 1.0 * (1024 ** 3):
            print(f"⚠️ [Deciia显存安全LoRA] 显存体检警告：空闲仅 {_gb(free_before)}，挂载后采样可能 OOM")

        current = model
        ok = 0
        lines = []
        for idx, (file, strength, mode, note) in enumerate(jobs, 1):
            tag = f"（{note}）" if note else ""
            if strength == 0.0:
                lines.append(f"▸ [{idx}] ⏸️ {file}{tag} 强度为 0，跳过")
                continue
            current, status = _apply_row(current, file, strength, mode)
            if status.startswith("✅"):
                ok += 1
            lines.append(
                f"▸ [{idx}] {file}{tag} @{strength:.2f} "
                f"[{mode}→{MODE_TABLE.get(mode, MODE_TABLE[MODE_STD])['apply']}] {status}"
            )

        free_after = comfy.model_management.get_free_memory()
        delta = (free_after - free_before) / (1024 ** 3)
        delta_str = f"{delta:+.2f} GB" if abs(delta) >= 0.005 else "≈0"
        print(
            f"[Deciia显存安全LoRA] ✅ 完成（成功 {ok}/{len(jobs)}）\n"
            + "──────────────────────────────\n"
            + "\n".join(lines) + "\n"
            + "──────────────────────────────\n"
            + f"▸ 空闲显存  : {_gb(free_before)} → {_gb(free_after)}  ({delta_str})"
        )
        return io.NodeOutput(current, stack_out)


NODE_CLASS_MAPPINGS = {"DeciiaVramSafeLoraStack": DeciiaVramSafeLoraStack}
NODE_DISPLAY_NAME_MAPPINGS = {"DeciiaVramSafeLoraStack": "🛡️ Deciia 显存安全 LoRA 串"}

# -*- coding: utf-8 -*-
"""
Deciia 显存安全 LoRA 串 v1（deciia 自建包 ComfyUI_Deciia_LoraStacks，作者标记 deciia）
================================================================================
2026-09-15 变更：用本节点替换存量工作流里的旧节点
（6× BanZhang_LoRA_VramSafe + 3× DeciiaLoraStack）。

= 班长面板 UX（banzhang_lora_loader v2.1）+ Deciia 旁路内核（deciia_lora_stack v12）合并 =
- 前端行式面板：每行 [启用] [LoRA文件(Filter+文件夹树弹层)] [强度] [⇄方式] [备注] [×]
- ⇄ 两态图标：灰空心=标准（权重融合） / 绿实心=旁路（前向叠加不改权重）
- 底部 [＋ 添加 LoRA] [↑ 上移] [↓ 下移]（点行选中，最多 16 行）
- 面板序列化为 JSON 经「LoRA配置」传入；「LoRA文件」+「模型强度」单 LoRA 兜底
- 后端：clone-first + 原生 patch / bypass + 不兼容自动跳过 + 显存体检日志
- 配置载体为单个 JSON widget（非动态槽控件），无 v12 位置灌值错位问题；
  prompt 提交由前端 serializeValue 兜底，位置错位免疫。

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
import folder_paths
from comfy_api.latest import io

from .lora_stack import _apply_model_lora

log = logging.getLogger(__name__)

MODE_STD = "标准"
MODE_BYPASS = "旁路"

# 前后端共用字面量；未知值一律回落标准（软失败，不中断出片）
_MODE_ALIASES = {
    MODE_STD: MODE_STD,
    "std": MODE_STD,
    "standard": MODE_STD,
    "normal": MODE_STD,
    MODE_BYPASS: MODE_BYPASS,
    "bypass": MODE_BYPASS,
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


def _parse_rows(raw) -> list:
    """解析面板 JSON -> [(文件, 强度, 方式, 备注), ...]。未启用/未选文件的行跳过。"""
    try:
        rows = json.loads(raw) if isinstance(raw, str) else (raw if isinstance(raw, list) else [])
        if not isinstance(rows, list):
            rows = []
    except Exception:
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


def _apply_row(model, file: str, strength: float, mode: str):
    """挂单行 LoRA。返回 (model, 状态描述)。内核同 v12 串：clone-first + patch/bypass + 不兼容跳过。"""
    path = folder_paths.get_full_path("loras", file)
    if path is None:
        return model, "❌ loras 目录下未找到（已跳过）"
    before = comfy.model_management.get_free_memory()
    out = _apply_model_lora(model, file, strength, mode)
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
            ],
            outputs=[
                io.Model.Output(display_name="模型"),
            ],
        )

    @classmethod
    def execute(cls, **kwargs) -> io.NodeOutput:
        model = kwargs.get("模型")
        if model is None:
            raise ValueError("[Deciia LoRA] 请连接 模型 输入。")

        jobs = _parse_rows(kwargs.get("LoRA配置"))
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
            return io.NodeOutput(model)

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
            lines.append(f"▸ [{idx}] {file}{tag} @{strength:.2f} [{mode}] {status}")

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
        return io.NodeOutput(current)


NODE_CLASS_MAPPINGS = {"DeciiaVramSafeLoraStack": DeciiaVramSafeLoraStack}
NODE_DISPLAY_NAME_MAPPINGS = {"DeciiaVramSafeLoraStack": "🛡️ Deciia 显存安全 LoRA 串"}

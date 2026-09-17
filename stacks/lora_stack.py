"""
Deciia 动态多 LoRA 串（deciia 自建包 ComfyUI_Deciia_LoraStacks，作者标记 deciia）

- 槽位可增删、可上移/下移排序，节点高度随槽位数自适应
- 每槽: 启用 + LoRA 文件 + 强度 + 方式（标准/旁路），按顺序叠加
- 单 MODEL 进/出
- 槽控件由前端真增删（litegraph 原生序列化），不预声明不隐藏

【为什么必须是 V3 节点 + accept_all_inputs（2026-09-11 定位并修复）】
  槽控件名字（启用_i / LoRA_i / 强度_i / 方式_i）由前端动态创建，不在 schema 声明里。
  后端 comfy/execution.py:get_input_data() 只把「已声明输入」或「连线」交给节点函数：
      elif input_category is not None or (is_v3 and class_def.ACCEPT_ALL_INPUTS)
  而 comfy_execution/graph.py:get_input_info() 对未声明的名字返回 (None, None, None)。
  → 旧的 legacy 写法（INPUT_TYPES 只声明 model）会让前端发来的全部槽位值被静默丢弃，
    节点每次都走「无生效槽位，模型原样输出」，LoRA 从来没挂上过。
  前端确实发了值（frontend src/utils/executionUtil.ts: graphToPrompt 会把所有
  widget.name → widget.value 写进 prompt），所以只需后端放行 —— V3 + accept_all_inputs。
  节点类型名保持 "DeciiaLoraStack" 不变，工作流与前端 JS 零改动。
"""
from __future__ import annotations

import logging

import comfy.sd
import comfy.utils
import folder_paths
from comfy_api.latest import io

log = logging.getLogger(__name__)

MAX_LORAS = 16


def _lora_list():
    try:
        names = list(folder_paths.get_filename_list("loras") or [])
    except Exception:
        names = []
    return ["none"] + names


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


class DeciiaLoraStack(io.ComfyNode):
    """单排动态 LoRA 串。

    后端 schema 只声明 model；槽控件（启用_i/LoRA_i/强度_i/方式_i）由前端真增删，
    槽数存节点 properties（工作流 JSON）。accept_all_inputs=True 让后端把这些
    动态控件值原样交给 execute(**kwargs) —— 这是槽位能真正生效的关键。
    """

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="DeciiaLoraStack",
            display_name="Deciia 动态多LoRA串",
            category="Deciia/LoRA",
            description=(
                "Deciia 动态多LoRA串：槽位可增删/排序，每槽可选 标准/旁路 挂载，按序叠加。"
                "（V3 节点：accept_all_inputs 接收前端动态槽控件）"
            ),
            accept_all_inputs=True,
            inputs=[
                io.Model.Input("model", optional=True, tooltip="待挂 LoRA 的模型。"),
            ],
            outputs=[
                io.Model.Output(display_name="model"),
            ],
        )

    @classmethod
    def execute(cls, model=None, **kwargs) -> io.NodeOutput:
        if model is None:
            raise ValueError("[Deciia LoRA] 请连接 model 输入。")

        valid = set(_lora_list())
        applied = []
        # 槽不存在=画布上没有对应控件；以 LoRA_i 控件为槽存在依据（accept_all_inputs 放行）
        for i in range(1, MAX_LORAS + 1):
            if f"LoRA_{i}" not in kwargs:
                continue
            enabled = bool(kwargs.get(f"启用_{i}", True))
            name = str(kwargs.get(f"LoRA_{i}", "none") or "none").strip()
            if name not in ("none", "None", "") and (
                _is_dirty_lora_name(name) or name not in valid
            ):
                log.warning("[Deciia LoRA] 槽%d 名异常或不存在「%s」→ none", i, name)
                name = "none"
            try:
                strength = float(kwargs.get(f"强度_{i}", 1.0))
            except Exception:
                strength = 1.0
            mode = str(kwargs.get(f"方式_{i}", "标准") or "标准")
            if mode not in ("标准", "旁路"):
                mode = "标准"
            if not enabled or name in ("none", "None", "") or abs(strength) < 1e-8:
                continue
            model = _apply_model_lora(model, name, strength, mode)
            applied.append(f"{name}@{strength:.2f}({mode})")

        if applied:
            log.info("[Deciia LoRA] 共 %d 个: %s", len(applied), " → ".join(applied))
        else:
            log.info("[Deciia LoRA] 无生效槽位，模型原样输出。")
        return io.NodeOutput(model)


NODE_CLASS_MAPPINGS = {"DeciiaLoraStack": DeciiaLoraStack}
NODE_DISPLAY_NAME_MAPPINGS = {"DeciiaLoraStack": "Deciia 动态多LoRA串"}

"""Deciia 分块 PASS 2 执行器（现居 ComfyUI_Deciia_All/minimax_h3/h3_sampling）

定位：P8a 里 SamplerCustomAdvanced("PASS 2 · decode output, not denoised_output") 的
1:1 替换壳。内部调用 T8 插件现成的 v4 Masked-Low-Sigma 分块执行器
(execute_chunked_two_pass_upscale)，不复制算法、不改 T8 源码。

设计要点（源码核对日期 2026-09-16，T8 v1.81.0 / 1464a9f）：
  1. 输入 noise/guider/sampler/sigmas/latent_image 与 SamplerCustomAdvanced 同名同型，
     输出 output/denoised_output 两口同形 → 工作流里可原位换节点、不重排线。
  2. 目标分辨率自动 = 输入 latent 尺寸 × VAE_DOWNSAMPLE(16)。内部学习型放大会走
     learned_upscale_h3_av_latent 的 noop 等尺寸路径（upscaler 不加载、不上传权重），
     放大仍由上游 #13 LearnedLatentUpscale 负责 → 严格"只替换 PASS 2"，不双采两次放大。
  3. model/positive/negative/cfg 从传入 guider 提取（见 _converted_to_raw：
     original_conds 是 convert_cond 后的 dict 列表，须逆变换为 [tensor, metadata]
     原始对，否则 T8 reanchor_conditioning 解包崩溃；model 须传 ModelPatcher）。
  4. v4 schema → 执行器自动 rebind_shape_bound_sampler：每个时间分块用
     rebind_dual_clock_sampler 按该块 packed 几何体重建 dual-clock sampler
     （T8 作者 09-14 在 issue #18 确认的机制）。
  5. 音频输出二选一（audio_output widget）：
       - refined_exp（默认，deciia 扩展）：执行器运行期间截获 sample_piece 产出的
         每段精修音频（T8 原生会丢弃），按各段的绝对音频区间对位拼回全长缓冲，
         重叠区线性交叉渐变 → 输出二采精修后的音频。
         背景：12G 预算下一采只有 ~0.5MP，原始音频杂音明显；原生全画幅 PASS 2
         的音频精修能力在分块路径上被 v4 的 joint_av_preserve_input 丢弃。
       - preserve_first_pass：T8 原生行为，原样返回一采音频（诊断基线）。
     精修拼接失败自动回退一采原声并打日志，绝不拖垮视频主流程。
  6. 空间策略内部固定 full_frame_safe（作者实测 independent_tiles_exp 有接缝，
     research-only，不暴露成参数以免误用）→ 每段时间块恰好 1 次 sample_piece 调用，
     截获顺序即时间顺序（len 校验兜底）。
  7. 掩码策略 inherit_if_present_else_generate_all：latent 带 noise_mask 就继承，
     没有就全画幅生成，不报错。

模块加载策略：优先复用 T8 插件已加载进 sys.modules 的
chunked_two_pass_upscale_advanced（真实路径比对）；未加载时用合成包 _deciia_t8_pkg
（目录名含连字符无法常规 import）按需加载。
"""
from __future__ import annotations

import importlib.util as _ilu
import json
import logging
import math
import os
import sys
import types as _types

import torch

from comfy_api.latest import io

log = logging.getLogger(__name__)

_T8_DIRNAME = "comfyui-minimax-h3-audio-T8"
_PKG_NAME = "_deciia_t8_pkg"
_CHUNKED_MOD = "chunked_two_pass_upscale_advanced"
_LOAD_ERROR: str | None = None

_DEFAULT_UPSCALER = "minimax_h3_latent_upscaler_3d_fp16.safetensors"
_VAE_DOWNSAMPLE = 16
_TEMPORAL_STRATEGIES = ["guarded_overlap_exp", "full_clip_safe"]
_AUDIO_OUTPUTS = ["refined_exp", "preserve_first_pass"]


def _t8_h3_dir() -> str:
    return os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", _T8_DIRNAME, "h3_t8")
    )


def _find_loaded(py_path: str):
    """在 sys.modules 里找同文件已加载的模块（真实路径比对），没有则 None。"""
    target = os.path.normcase(os.path.realpath(py_path))
    for mod in list(sys.modules.values()):
        f = getattr(mod, "__file__", None)
        if not f:
            continue
        try:
            if os.path.normcase(os.path.realpath(f)) == target:
                return mod
        except OSError:
            continue
    return None


def _load_into_pkg(h3_dir: str, mod_name: str):
    key = f"{_PKG_NAME}.{mod_name}"
    if key in sys.modules:
        return sys.modules[key]
    if _PKG_NAME not in sys.modules:
        pkg = _types.ModuleType(_PKG_NAME)
        pkg.__path__ = [h3_dir]
        sys.modules[_PKG_NAME] = pkg
    spec = _ilu.spec_from_file_location(key, os.path.join(h3_dir, f"{mod_name}.py"))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot build import spec for {key}")
    module = _ilu.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


def _get_chunked_module():
    """返回 T8 的 chunked_two_pass_upscale_advanced 模块（复用优先，合成包兜底）。"""
    global _LOAD_ERROR
    h3_dir = _t8_h3_dir()
    chunked_py = os.path.join(h3_dir, f"{_CHUNKED_MOD}.py")
    if not os.path.exists(chunked_py):
        _LOAD_ERROR = (
            f"{_T8_DIRNAME}/h3_t8/{_CHUNKED_MOD}.py not found under custom_nodes"
        )
        raise RuntimeError(_LOAD_ERROR)

    loaded = _find_loaded(chunked_py)
    if loaded is not None and hasattr(loaded, "execute_chunked_two_pass_upscale"):
        return loaded

    try:
        mod = _load_into_pkg(h3_dir, _CHUNKED_MOD)
    except Exception as exc:  # noqa: BLE001
        _LOAD_ERROR = f"failed to load T8 {_CHUNKED_MOD}: {exc}"
        log.error("[Deciia] %s", _LOAD_ERROR)
        raise RuntimeError(_LOAD_ERROR) from exc

    if not hasattr(mod, "execute_chunked_two_pass_upscale"):
        _LOAD_ERROR = f"T8 {_CHUNKED_MOD} lacks execute_chunked_two_pass_upscale"
        raise RuntimeError(_LOAD_ERROR)
    return mod


def _nested_av_parts(latent):
    """从 latent dict 取 (video, audio)，兼容 NestedTensor 与普通张量。"""
    samples = latent.get("samples") if isinstance(latent, dict) else None
    if samples is None:
        raise ValueError(
            "DeciiaChunkedPass2SamplerLegacy: latent_image 缺少 samples；"
            "请接 T8 reconcile / learned upscale 输出的 H3 AV latent"
        )
    if getattr(samples, "is_nested", False):
        tensors = samples.tensors
        if len(tensors) != 2:
            raise ValueError(
                f"DeciiaChunkedPass2SamplerLegacy: 期望 H3 AV 双张量 nested latent，"
                f"实际 {len(tensors)} 个"
            )
        return tensors[0], tensors[1]
    raise ValueError(
        "DeciiaChunkedPass2SamplerLegacy: latent_image 不是 NestedTensor AV latent"
        "（缺 audio 分量）；请从 T8 双采链取 latent"
    )


def _converted_to_raw(converted):
    """convert_cond() 的逆变换：dict 列表 → [tensor, metadata] 原始对列表。

    convert_cond（comfy/sampler_helpers.py）做的是：
      meta = c[1].copy(); 若 c[0] 非 None 则 meta["cross_attn"] = c[0]; 再塞 uuid。
    逆变换把 cross_attn 放回元素 0，其余键保留在 metadata。T8 执行器终点
    sample_piece → _build_guider → inner_set_conds 会再做一次 convert_cond
    正向转换，round-trip 安全。
    """
    raw = []
    for item in converted:
        if isinstance(item, dict):
            meta = dict(item)
            tensor = meta.pop("cross_attn", None)
            raw.append([tensor, meta])
        else:  # 未转换的原始 [tensor, meta]（防御：直接抄前两个元素）
            raw.append(list(item[:2]))
    return raw


def _raw_conditioning_from_guider(guider):
    """从 guider 提取 (positive_raw, negative_raw, cfg, model_patcher)。"""
    conds = getattr(guider, "original_conds", None) or getattr(
        guider, "conds", None
    ) or {}
    positive_conv = conds.get("positive")
    negative_conv = conds.get("negative")
    if not positive_conv:
        raise ValueError(
            "DeciiaChunkedPass2SamplerLegacy: guider 上找不到 positive 条件"
            "（支持 BasicGuider / CFGGuider）"
        )

    positive = _converted_to_raw(positive_conv)
    negative = _converted_to_raw(negative_conv) if negative_conv else None
    cfg = float(getattr(guider, "cfg", 1.0) or 1.0)
    # T8 执行器的 model 参数 = ModelPatcher：_build_guider 直接 CFGGuider(model_patcher)、
    # prepare_callback(guider.model_patcher, ...)；传裸模型会 AttributeError。
    model = guider.model_patcher
    return positive, negative, cfg, model


def _executor_segments(mod, input_video, plan):
    """复刻执行器的分段时间轴选择逻辑，返回与采样顺序一致的 segment 列表。"""
    tokens = int(input_video.shape[2])
    frame_count = mod.frames_for_tokens(tokens)
    if plan.get("temporal_strategy", "full_clip_safe") == "full_clip_safe":
        return [(0, 0, tokens, frame_count)]
    segments, _fc = mod.compute_temporal_segments(
        tokens,
        int(plan["temporal_chunk_frames"]),
        int(plan["temporal_overlap_frames"]),
    )
    return segments


def _energy_gate(input_audio: torch.Tensor, kernel: int = 64) -> torch.Tensor:
    """按输入音频能量算逐帧门控权重 w∈[0,1]：活跃区≈1（信精修），静音区≈0（保原声）。

    目的：尾块/静音段上，低σ二采可能"幻觉"出输入里不存在的持续声音
    （实测：输入尾部已衰减到 ~100 RMS，精修输出却持续 ~5000 RMS 直到硬切）。
    能量门控让修复只作用于有声音的区域，静音保持静音。
    """
    mono = input_audio.abs().mean(dim=(0, 1))  # (L,)
    L = mono.shape[-1]
    k = max(1, min(kernel, L))
    pad = k // 2
    e = torch.nn.functional.avg_pool1d(
        mono.view(1, 1, -1), kernel_size=k, stride=1, padding=pad
    ).view(-1)[..., :L]
    sorted_e, _ = torch.sort(e)
    floor = float(sorted_e[int(0.2 * len(sorted_e))])
    level = float(sorted_e[int(0.9 * len(sorted_e))])
    if level <= floor * 1.5:
        # 能量平坦（整段全响或全静）：不门控，完全信任精修
        return torch.ones_like(e)
    w = (e - floor * 2.0) / max(1e-6, level - floor * 2.0)
    w = w.clamp(0.0, 1.0)
    w = torch.nn.functional.avg_pool1d(
        w.view(1, 1, -1), kernel_size=k, stride=1, padding=pad
    ).view(-1)[..., :L]
    return w


def _merge_audio_segments(input_audio, segments, refined_chunks, rescale):
    """把各段精修音频按绝对音频区间拼回全长缓冲；段间重叠区线性交叉渐变；
    最后按输入能量门控混合（活跃=精修，静音=原声），杜绝静音段幻觉音。

    input_audio: (1, 32, 2, L) 一采音频（定形状基准）
    segments:    [(start_token, start_frame, end_token, end_frame), ...] 时间顺序
    refined_chunks: 与 segments 等长的精修音频张量列表（形状=对应段的输入音频切片）
    返回: (merged, placed_count)；任何形状对不上直接抛异常（调用方回退）。
    """
    total = int(input_audio.shape[-1])
    dev, dt = input_audio.device, input_audio.dtype
    if len(refined_chunks) != len(segments):
        raise RuntimeError(
            f"refined chunks={len(refined_chunks)} != segments={len(segments)}"
        )
    placed_buf = torch.zeros_like(input_audio)
    weight = torch.zeros(
        (1, 1, 1, total), dtype=torch.float32, device=dev
    )
    prev_end: int | None = None
    placed = 0
    for seg, ref in zip(segments, refined_chunks):
        _st, start_frame, _et, end_frame = seg
        a0 = int(round(start_frame * rescale))
        a1 = min(total, int(round(end_frame * rescale)))
        if a1 <= a0:
            continue
        ref = ref.to(device=dev, dtype=dt)
        span = input_audio[..., a0:a1]
        if tuple(ref.shape) != tuple(span.shape):
            raise RuntimeError(
                f"refined audio shape {tuple(ref.shape)} != span {tuple(span.shape)}"
            )
        if prev_end is None or a0 >= prev_end:
            placed_buf[..., a0:a1] = ref
            weight[..., a0:a1] = 1.0
        else:
            ov = min(prev_end - a0, a1 - a0)
            t = torch.linspace(0.0, 1.0, ov, device=dev, dtype=dt).view(
                1, 1, 1, ov
            )
            placed_buf[..., a0 : a0 + ov] = (
                placed_buf[..., a0 : a0 + ov] * (1.0 - t) + ref[..., :ov] * t
            )
            weight[..., a0 : a0 + ov] = torch.maximum(
                weight[..., a0 : a0 + ov], t
            )
            placed_buf[..., a0 + ov : a1] = ref[..., ov:]
            weight[..., a0 + ov : a1] = 1.0
        prev_end = a1
        placed += 1
    if placed == 0 or prev_end is None:
        raise RuntimeError("no refined audio placed")
    # 能量门控：活跃区信精修，静音区保原声（防尾块幻觉音/静音段噪声放大）
    gate = _energy_gate(input_audio).to(device=dev, dtype=dt).view(1, 1, 1, -1)
    eff = gate * weight  # 未被任何精修段覆盖的区域恒回原声
    merged = input_audio * (1.0 - eff) + placed_buf * eff
    return merged, placed


def _execute_with_refined_audio(
    mod,
    model_patcher,
    positive,
    latent_image,
    noise,
    sampler,
    sigmas,
    plan,
    negative,
    cfg,
    input_video,
    input_audio,
):
    """运行 v4 执行器并截获每段精修音频，输出 latent 的音频替换为拼接结果。

    通过临时代换 T8 模块的 sample_piece（执行器内部以模块全局引用调用，晚绑定
    生效）；无论成败 finally 还原。音频截获/拼接任何一步失败都回退 T8 原生输出
    （=一采原声），不影响视频。
    """
    original_sample_piece = mod.sample_piece
    captured: list = []

    def capturing_sample_piece(piece, *args, **kwargs):
        out = original_sample_piece(piece, *args, **kwargs)
        try:
            if getattr(out, "is_nested", False) and len(out.tensors) == 2:
                captured.append(out.tensors[1].detach().clone())
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "[Deciia ChunkedPass2] refined audio capture failed (skipped): %s",
                exc,
            )
        return out

    mod.sample_piece = capturing_sample_piece
    try:
        output, report_json = mod.execute_chunked_two_pass_upscale(
            model_patcher,
            positive,
            latent_image,
            noise,
            sampler,
            sigmas,
            plan,
            negative=negative,
            cfg=cfg,
        )
    finally:
        mod.sample_piece = original_sample_piece

    try:
        segments = _executor_segments(mod, input_video, plan)
        merged, placed = _merge_audio_segments(
            input_audio,
            segments,
            captured,
            getattr(mod, "FRAME_RESCALE", 5.0 / 3.0),
        )
        samples_obj = output.get("samples")
        if not getattr(samples_obj, "is_nested", False) or len(samples_obj.tensors) != 2:
            raise RuntimeError("output latent is not a 2-tensor nested AV latent")
        import comfy.nested_tensor as _nt

        refined_output = dict(output)
        refined_output["samples"] = _nt.NestedTensor(
            (samples_obj.tensors[0], merged)
        )
        log.info(
            "[Deciia ChunkedPass2] refined audio: captured=%s placed=%s "
            "(output audio = second-pass refined)",
            len(captured),
            placed,
        )
        return refined_output, report_json
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "[Deciia ChunkedPass2] refined-audio merge failed, falling back to "
            "first-pass audio: %s",
            exc,
        )
        return output, report_json


class DeciiaChunkedPass2SamplerLegacy(io.ComfyNode):
    """T8 v4 时间分块低Sigma二采的 deciia 壳（1:1 替换 SamplerCustomAdvanced PASS 2）。命名后缀 Legacy：T8 v1.85.0 起官方内置同名节点 DeciiaChunkedPass2Sampler（原生适配实现），本节点改名共存、互不遮蔽，供参考/对照使用。"""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="DeciiaChunkedPass2SamplerLegacy",
            display_name="Deciia 分块PASS2 Legacy (T8 v4·时间分块·全画幅·参考实现)",
            category="Deciia/Sampling",
            description=(
                "Drop-in replacement for the PASS 2 SamplerCustomAdvanced. "
                "Runs the T8 v4 masked low-sigma chunked executor: temporal "
                "chunking + per-piece dual-clock sampler rebind, full-frame "
                "spatial (no seams). Target size auto-derives from the input "
                "latent (internal learned upscale stays noop). audio_output="
                "'refined_exp' (deciia extension) stitches the per-segment "
                "second-pass refined audio that T8 would discard, so the "
                "output keeps PASS 2 audio repair; 'preserve_first_pass' is "
                "the stock T8 behavior (first-pass audio by identity). "
                "denoised_output is a placeholder equal to output - decode "
                "'output', never 'denoised_output'."
            ),
            inputs=[
                io.Noise.Input("noise"),
                io.Guider.Input(
                    "guider",
                    tooltip="PASS 2 guider（如 HIGH guider / DetailMixer 链的 BasicGuider）",
                ),
                io.Sampler.Input(
                    "sampler",
                    tooltip="T8 dual-clock sampler（v4 schema 会按分块几何自动 rebind）",
                ),
                io.Sigmas.Input("sigmas"),
                io.Latent.Input(
                    "latent_image",
                    tooltip="T8 reconcile/upscale 后的 H3 AV latent；目标尺寸=该 latent×16",
                ),
                io.Combo.Input(
                    "temporal_strategy",
                    options=_TEMPORAL_STRATEGIES,
                    default="guarded_overlap_exp",
                    tooltip=(
                        "guarded_overlap_exp=按块切时间轴（省显存，作者标EXP）；"
                        "full_clip_safe=整段一块（作者验证基线，不省显存）"
                    ),
                ),
                io.Int.Input(
                    "temporal_chunk_frames",
                    default=34,
                    min=17,
                    max=3600,
                    step=17,
                    tooltip="每块帧数（full_clip_safe 下无效）",
                ),
                io.Int.Input(
                    "temporal_overlap_frames",
                    default=17,
                    min=0,
                    max=1700,
                    step=17,
                    tooltip="块间重叠帧数（full_clip_safe 下无效）",
                ),
                io.Float.Input(
                    "anchor_strength",
                    default=0.999,
                    min=0.0,
                    max=1.0,
                    step=0.001,
                ),
                io.Combo.Input(
                    "audio_output",
                    options=_AUDIO_OUTPUTS,
                    default="refined_exp",
                    tooltip=(
                        "refined_exp=拼接各段二采精修音频（deciia 扩展，修一采杂音）；"
                        "preserve_first_pass=T8 原生行为（原样返回一采音频）"
                    ),
                ),
            ],
            outputs=[
                io.Latent.Output("output"),
                io.Latent.Output(
                    "denoised_output",
                    tooltip="占位口（=output）。解码请永远接 output。",
                ),
            ],
        )

    @classmethod
    def execute(
        cls,
        noise,
        guider,
        sampler,
        sigmas,
        latent_image,
        temporal_strategy="guarded_overlap_exp",
        temporal_chunk_frames=34,
        temporal_overlap_frames=17,
        anchor_strength=0.999,
        audio_output="refined_exp",
    ):
        mod = _get_chunked_module()

        video, audio = _nested_av_parts(latent_image)
        if video.ndim != 5:
            raise ValueError(
                f"DeciiaChunkedPass2SamplerLegacy: video latent 应为 5D，"
                f"实际 {video.ndim}D shape={tuple(video.shape)}"
            )
        target_width = int(video.shape[-1]) * _VAE_DOWNSAMPLE
        target_height = int(video.shape[-2]) * _VAE_DOWNSAMPLE

        plan, _plan_json = mod.build_chunked_two_pass_masked_low_sigma_plan(
            model_name=_DEFAULT_UPSCALER,
            target_width=target_width,
            target_height=target_height,
            temporal_chunk_frames=int(temporal_chunk_frames),
            temporal_overlap_frames=int(temporal_overlap_frames),
            anchor_strength=float(anchor_strength),
            tile_width=target_width,
            tile_height=target_height,
            spatial_overlap=0,
            spatial_fade=0,
            minimum_tile_size=256,
            overlap_blend="smoothstep",
            precision="fp16",
            release_policy="offload_after",
            spatial_strategy="full_frame_safe",
            temporal_strategy=temporal_strategy,
            second_pass_audio_policy="joint_av_preserve_input",
            video_mask_policy="inherit_if_present_else_generate_all",
        )

        positive, negative, cfg, model = _raw_conditioning_from_guider(guider)

        if audio_output == "refined_exp":
            output, report_json = _execute_with_refined_audio(
                mod,
                model,
                positive,
                latent_image,
                noise,
                sampler,
                sigmas,
                plan,
                negative,
                cfg,
                video,
                audio,
            )
        else:
            output, report_json = mod.execute_chunked_two_pass_upscale(
                model,
                positive,
                latent_image,
                noise,
                sampler,
                sigmas,
                plan,
                negative=negative,
                cfg=cfg,
            )
        try:
            report = json.loads(report_json)
            log.info(
                "[Deciia ChunkedPass2] segments=%s pieces=%s status=%s "
                "audio_output=%s",
                report.get("segment_count"),
                len(report.get("tiles") or []),
                report.get("status"),
                audio_output,
            )
        except Exception:  # noqa: BLE001
            pass
        return io.NodeOutput(output, output)


NODE_CLASS_MAPPINGS = {"DeciiaChunkedPass2SamplerLegacy": DeciiaChunkedPass2SamplerLegacy}
NODE_DISPLAY_NAME_MAPPINGS = {
    "DeciiaChunkedPass2SamplerLegacy": "Deciia 分块PASS2 Legacy (T8 v4·时间分块·全画幅·参考实现)"
}

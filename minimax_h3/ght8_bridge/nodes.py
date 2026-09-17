"""Deciia GHT8Bridge — MiniMax-H3 智能一体化(GH) → T8 双采条件桥（自建插件，作者标记 deciia）。

本包 = custom_nodes/ComfyUI_Deciia_GHT8Bridge（2026-09-14 新建）。

================================================================================
设计目标 / 边界
================================================================================
把 Goohai-MiniMax-H3_Integration（导演台「MiniMax-H3 智能一体化(GH)」+「适配器(GH)」）
的素材面板体验，接进 T8 双采链，替代两个 MiniMaxH3AudioConditioningT8。

- 入口 socket 与「MiniMax-H3 适配器(GH)」完全相同：io.Custom("MiniMax") 私有类型，
  按类型名字符串跨插件匹配 → 直接把导演台的 ">" 输出拖到本节点即可，导演台零改动。
- 条件编码在运行时动态加载当前安装的 comfyui-minimax-h3-audio-T8 的
  build_conditioning（随 T8 更新自动使用新实现，T8 改布局也不会失效）。
- 输出槽位与 MiniMaxH3AudioConditioningT8 完全一致
  （positive / av_latent / mux_audio / conditioned_prompt / media_map_json / report），
  可原位替换工作流里任意一个 AudioConditioningT8。

素材来源（关键设计）：
  导演台把素材面板状态存在自己的 `gh_state_json` widget 里（不是输出口），
  结构为 {"mode":…, "prompts":{…}, "media":[[槽位, {"name":文件名, "kind":…}], …]}。
  本节点用 ComfyUI 官方隐藏输入 PROMPT / EXTRA_PNGINFO 在后端读整张图：
    ① 顺着自己的 integration 连线找到上游「智能一体化(GH)」节点，读它的 gh_state_json；
    ② 连线被 Set/Get 总线挡住或找不到时，全图搜 MiniMaxH3IntegrationGH；
    ③ 再兜底扫 extra_pnginfo["workflow"] 的 UI json（widgets_values 里的同名字符串）。
  素材文件用 ComfyUI 官方加载器加载（LoadImage / LoadAudio / VideoFromFile），
  与工作流里手动 LoadImage 同源。

错误策略（2026-09-14 定稿）：**报错不降级**。
  - 找不到导演台 / 读不到 gh_state_json → 直接 ValueError 中止；
  - 素材条目存在但文件加载失败 → 直接 ValueError（含文件名）；
  - 不做「静默跳过素材继续出片」的降级。

更新策略判断（供维护者参考）：
  1. comfyui-minimax-h3-audio-T8 更新：
     - build_conditioning 签名不变 → 零改动（自动跟随）；
     - 若签名变化 → 运行时报错会指明缺失/多余参数，按报错调整 execute 的调用即可。
  2. Goohai-MiniMax-H3_Integration 更新：
     - gh_state_json 的 media 结构不变（[槽位, {name, kind}]）→ 零改动；
     - 若结构变化 → 只需改本插件的 _parse_state / _resolve_media_state 解析层。
  3. 本插件不 import 任何第三方插件代码作为依赖（T8 用动态加载），删除本包不影响既有工作流。

本插件只注册 1 个节点；与 RefT8Bridge（Ref→T8 桥）互不影响。
================================================================================
"""
from __future__ import annotations

import importlib.util
import json
import logging
import sys
from pathlib import Path

import numpy as np
import torch

import folder_paths
from comfy_api.latest import io

log = logging.getLogger("deciia.ght8bridge")

IntegrationBundle = io.Custom("MiniMax")   # 与「MiniMax-H3 适配器(GH)」同一 socket 类型

# ---------------------------------------------------------------- T8 条件模块加载
# T8 插件的运行时根目录随版本变化（新版把实现放在 h3_t8/ 下，旧版/内层包也在候选中），
# 因此这里先按“已加载模块”复用，再按候选目录顺序合成包加载。
_T8_PLUGIN_DIRNAME = "comfyui-minimax-h3-audio-T8"
_T8_ROOT_CANDIDATES = ("h3_t8", "comfyui-minimax-h3-audio-T8", ".")
_T8_PKG = "_deciia_ght8_t8_pkg"
_T8_COND_MODULE = None
_T8_LOAD_ERROR: str | None = None


def _t8_plugin_dir() -> Path:
    return Path(__file__).resolve().parent.parent / _T8_PLUGIN_DIRNAME


def _find_loaded_t8_conditioning():
    """sys.modules 里已加载、且属于 T8 插件目录的 conditioning 模块 → 直接复用。"""
    plugin_dir = str(_t8_plugin_dir()).replace("\\", "/").lower()
    for mod in list(sys.modules.values()):
        path = getattr(mod, "__file__", None)
        if not path or not hasattr(mod, "build_conditioning"):
            continue
        if plugin_dir in str(path).replace("\\", "/").lower():
            return mod
    return None


def _load_t8_conditioning():
    """返回 T8 的 conditioning 模块（含 build_conditioning）。"""
    global _T8_COND_MODULE, _T8_LOAD_ERROR
    if _T8_COND_MODULE is not None:
        return _T8_COND_MODULE
    if _T8_LOAD_ERROR:
        raise RuntimeError(_T8_LOAD_ERROR)

    loaded = _find_loaded_t8_conditioning()
    if loaded is not None:
        _T8_COND_MODULE = loaded
        return loaded

    plugin_dir = _t8_plugin_dir()
    root = None
    for candidate in _T8_ROOT_CANDIDATES:
        probe = (plugin_dir / candidate).resolve() if candidate != "." else plugin_dir.resolve()
        if (probe / "conditioning.py").exists():
            root = probe
            break
    if root is None:
        _T8_LOAD_ERROR = (
            f"未找到 {_T8_PLUGIN_DIRNAME}/**/conditioning.py（T8 插件缺失或目录结构变化）；"
            "本节点依赖 T8 的 build_conditioning 做条件编码。"
        )
        raise RuntimeError(_T8_LOAD_ERROR)

    try:
        if _T8_PKG not in sys.modules:
            pkg = type(sys)(_T8_PKG)
            pkg.__path__ = [str(root)]
            sys.modules[_T8_PKG] = pkg
        for mod in ("core", "prompt_tags", "conditioning"):
            key = f"{_T8_PKG}.{mod}"
            if key in sys.modules:
                continue
            spec = importlib.util.spec_from_file_location(key, root / f"{mod}.py")
            if spec is None or spec.loader is None:
                raise RuntimeError(f"无法为 T8 模块 {mod} 建 spec")
            module = importlib.util.module_from_spec(spec)
            sys.modules[key] = module
            spec.loader.exec_module(module)
        _T8_COND_MODULE = sys.modules[f"{_T8_PKG}.conditioning"]
        log.info("[DeciiaGHT8Bridge] 已加载 T8 conditioning：%s", root)
        return _T8_COND_MODULE
    except Exception as exc:  # noqa: BLE001
        _T8_LOAD_ERROR = f"加载 T8 conditioning 失败：{exc}"
        raise


# ------------------------------------------------------------ 导演台素材状态解析
def _usable_state(state) -> bool:
    """真状态判断：非空字符串、且不是 ComfyUI 回放空 widget 时的字面量 "(none)"。"""
    return isinstance(state, str) and bool(state.strip()) and state.strip() != "(none)"


def _state_has_media(state_json: str) -> bool:
    try:
        state = json.loads(state_json)
    except (TypeError, ValueError):
        return False
    media = state.get("media") if isinstance(state, dict) else None
    return isinstance(media, list) and len(media) > 0


def _state_from_api_prompt(prompt, uid):
    """从 API prompt 里取「智能一体化(GH)」节点的 gh_state_json。"""
    if not isinstance(prompt, dict):
        return None, None
    # ① 顺本节点的 integration 连线找上游
    if uid is not None:
        me = prompt.get(str(uid))
        if isinstance(me, dict):
            link = (me.get("inputs") or {}).get("integration")
            if isinstance(link, (list, tuple)) and len(link) >= 1:
                src = prompt.get(str(link[0]))
                if isinstance(src, dict) and src.get("class_type") == "MiniMaxH3IntegrationGH":
                    state = (src.get("inputs") or {}).get("gh_state_json")
                    if _usable_state(state):
                        return state, str(link[0])
    # ② 全图扫（Set/Get 总线、间接连线等情况）
    fallback = None
    for nid, node in prompt.items():
        if not isinstance(node, dict) or node.get("class_type") != "MiniMaxH3IntegrationGH":
            continue
        state = (node.get("inputs") or {}).get("gh_state_json")
        if not _usable_state(state):
            continue
        if _state_has_media(state):
            return state, str(nid)
        if fallback is None:
            fallback = (state, str(nid))
    return fallback if fallback else (None, None)


def _state_from_ui_workflow(extra_pnginfo):
    """兜底：从 extra_pnginfo 的 UI json 里取导演台节点的 gh_state_json。"""
    workflow = extra_pnginfo.get("workflow") if isinstance(extra_pnginfo, dict) else None
    if not isinstance(workflow, dict):
        return None, None
    fallback = None
    for node in workflow.get("nodes") or []:
        if not isinstance(node, dict) or node.get("type") != "MiniMaxH3IntegrationGH":
            continue
        candidates = []
        named = node.get("widgets_values_named")
        if isinstance(named, dict) and _usable_state(named.get("gh_state_json")):
            candidates.append(named["gh_state_json"])
        # 导演台前端把同一份状态同步写进 node.properties（gh_h3_state），
        # extra_pnginfo 里 properties 与 widgets_values 都可能命中，两处都扫。
        properties = node.get("properties")
        if isinstance(properties, dict):
            for value in properties.values():
                if _usable_state(value) and value.lstrip().startswith("{") and '"media"' in value:
                    candidates.append(value)
        for value in node.get("widgets_values") or []:
            if _usable_state(value) and value.lstrip().startswith("{") and '"media"' in value:
                candidates.append(value)
        for state in candidates:
            if _state_has_media(state):
                return state, str(node.get("id"))
            if fallback is None:
                fallback = (state, str(node.get("id")))
    return fallback if fallback else (None, None)


def _resolve_media_state(cls):
    """返回 (state_json, 来源节点ID)。找不到就按“报错不降级”策略抛错。"""
    hidden = getattr(cls, "hidden", None)
    prompt = getattr(hidden, "prompt", None)
    uid = getattr(hidden, "unique_id", None)
    extra_pnginfo = getattr(hidden, "extra_pnginfo", None)

    state, node_id = _state_from_api_prompt(prompt, uid)
    if not state:
        state, node_id = _state_from_ui_workflow(extra_pnginfo)
    if not state:
        raise ValueError(
            "[Deciia 参考引导 (GH→T8 桥)] 读不到导演台素材状态：\n"
            "  · 请把「MiniMax-H3 智能一体化(GH)」的输出（>）接到本节点的 integration 口；\n"
            "  · 或确认工作流里存在该导演台节点且已被前端保存过（gh_state_json 需要在图里）；\n"
            "  · 纯 API 提交（无 extra_pnginfo/PROMPT）时本节点无法读取导演台素材面板，属预期限制。"
        )
    return state, node_id


def _parse_state(state_json):
    """gh_state_json → (state_dict, media_dict{槽位: entry})。"""
    try:
        state = json.loads(state_json)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"[Deciia 参考引导 (GH→T8 桥)] 导演台状态 JSON 解析失败：{exc}") from exc
    if not isinstance(state, dict):
        raise ValueError("[Deciia 参考引导 (GH→T8 桥)] 导演台状态不是 JSON 对象。")
    media = {}
    for item in state.get("media") or []:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            slot, entry = item
            if isinstance(slot, str) and isinstance(entry, dict):
                media[slot] = entry
    return state, media


def _slot_filename(entry) -> str:
    name = entry.get("name") if isinstance(entry, dict) else None
    if isinstance(name, str) and name.strip() and name != "(none)":
        return name.strip()
    return ""


def _mode_model_number(mode) -> int:
    """与「MiniMax-H3 适配器(GH)」同语义：all_reference → 1，其余（text_keyframes）→ 0。"""
    return 1 if mode == "all_reference" else 0


def _state_prompt(state, mode: str) -> str:
    """导演台提示词：prompts[mode] 优先，其次 state['prompt']。"""
    prompts = state.get("prompts")
    if isinstance(prompts, dict) and isinstance(prompts.get(mode), str) and prompts[mode].strip():
        return prompts[mode]
    if isinstance(state.get("prompt"), str) and state["prompt"].strip():
        return state["prompt"]
    return ""


# ------------------------------------------------------------------ 素材加载层
def _load_image(name: str):
    """文件名 → IMAGE 张量 [1,H,W,C]（官方 LoadImage 同源；失败即报错）。"""
    try:
        import nodes as comfy_nodes
        image, _mask = comfy_nodes.LoadImage().load_image(name)
        if image is not None:
            return image
    except Exception as exc:  # noqa: BLE001
        log.info("[DeciiaGHT8Bridge] LoadImage 加载 %r 失败，改用 PIL：%s", name, exc)
    try:
        from PIL import Image, ImageOps
        path = folder_paths.get_annotated_filepath(name)
        with Image.open(path) as im:
            img = ImageOps.exif_transpose(im)
            if img.mode == "I":
                img = img.point(lambda v: v * (1 / 255))
            arr = np.array(img.convert("RGB")).astype(np.float32) / 255.0
        return torch.from_numpy(arr)[None, ...]
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"[Deciia 参考引导 (GH→T8 桥)] 参考图加载失败：{name}（{exc}）") from exc


def _load_audio(name: str):
    """文件名 → AUDIO dict {waveform[B,C,L], sample_rate}（失败即报错）。"""
    try:
        from comfy_extras import nodes_audio
        audio = nodes_audio.LoadAudio.load(name)[0]
        if isinstance(audio, dict) and audio.get("waveform") is not None:
            return audio
    except Exception as exc:  # noqa: BLE001
        log.info("[DeciiaGHT8Bridge] LoadAudio 加载 %r 失败，改用 PyAV：%s", name, exc)
    try:
        import av
        path = folder_paths.get_annotated_filepath(name)
        with av.open(path) as container:
            if not container.streams.audio:
                raise ValueError("文件不含音频流")
            stream = container.streams.audio[0]
            sample_rate = int(stream.codec_context.sample_rate)
            channels = stream.channels
            chunks = []
            for frame in container.decode(streams=stream.index):
                buf = torch.from_numpy(frame.to_ndarray())
                if buf.shape[0] != channels:
                    buf = buf.view(-1, channels).t()
                chunks.append(buf)
            if not chunks:
                raise ValueError("解码结果为空")
            waveform = torch.cat(chunks, dim=1)
            if waveform.dim() == 1:
                waveform = waveform.unsqueeze(0)
            if waveform.dim() == 2:
                waveform = waveform.unsqueeze(0)
            return {"waveform": waveform.float(), "sample_rate": sample_rate}
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"[Deciia 参考引导 (GH→T8 桥)] 音频加载失败：{name}（{exc}）") from exc


def _align_frame_count_down(count: int) -> int:
    """向上取整落到 H3 合法帧数网格 17n+5（向下对齐）。"""
    count = int(count)
    if count < 5:
        return 0
    return ((count - 5) // 17) * 17 + 5


def _sample_video_frames(frames, source_fps, target_frames):
    """参考视频按源播放速度折算到 24fps，并裁到目标帧数窗口（语义与导演台一致）。"""
    if frames is None or frames.shape[0] <= 1:
        return frames
    source_fps = float(source_fps or 0)
    requested = max(1, int(target_frames))
    if source_fps > 0:
        source_duration_frames = max(1, round(frames.shape[0] * 24 / source_fps))
        target_frames = min(requested, source_duration_frames)
    else:
        target_frames = min(requested, frames.shape[0])
    if source_fps <= 0:
        source_indices = torch.arange(target_frames, dtype=torch.long)
    else:
        source_indices = torch.floor(
            torch.arange(target_frames, dtype=torch.float32) * source_fps / 24
        ).long()
    source_indices = source_indices.clamp(max=frames.shape[0] - 1)
    return frames[source_indices][:target_frames]


def _load_video(name: str, target_frames: int):
    """文件名 → (frames IMAGE 张量, 音轨 AUDIO dict|None)（失败即报错）。"""
    try:
        from comfy_api.latest._input_impl import VideoFromFile
        path = folder_paths.get_annotated_filepath(name)
        components = VideoFromFile(path).get_components()
        frames = _sample_video_frames(components.images, components.frame_rate, target_frames)
        return frames, getattr(components, "audio", None)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"[Deciia 参考引导 (GH→T8 桥)] 参考视频加载失败：{name}（{exc}）") from exc


def _apply_audio_selection(audio, selection):
    """按导演台保存的 trimStart/trimEnd 裁音频（不改采样率）。"""
    if audio is None or not selection:
        return audio
    waveform = audio["waveform"]
    sample_rate = int(audio["sample_rate"])
    start_seconds, end_seconds = selection
    start_sample = max(0, min(waveform.shape[-1], round(float(start_seconds) * sample_rate)))
    end_sample = max(start_sample + 1, min(waveform.shape[-1], round(float(end_seconds) * sample_rate)))
    return {"waveform": waveform[..., start_sample:end_sample], "sample_rate": sample_rate}


def _trim_audio_to_duration(audio, duration_seconds):
    """裁/补到指定时长（同采样率）。"""
    if audio is None:
        return None
    waveform = audio["waveform"]
    sample_rate = int(audio["sample_rate"])
    sample_count = max(1, round(float(duration_seconds) * sample_rate))
    trimmed = waveform[..., :sample_count]
    if trimmed.shape[-1] < sample_count:
        trimmed = torch.nn.functional.pad(trimmed, (0, sample_count - trimmed.shape[-1]))
    return {"waveform": trimmed, "sample_rate": sample_rate}


def _audio_trim_ranges(media) -> dict:
    """导演台非破坏性音频选择 → {槽位: (start, end)}。"""
    ranges = {}
    for slot, entry in media.items():
        if entry.get("kind") != "audio":
            continue
        try:
            start = max(0.0, float(entry.get("trimStart", 0) or 0))
            raw_end = entry.get("trimEnd")
            end = float(raw_end) if raw_end is not None else None
        except (TypeError, ValueError):
            continue
        if end is not None and end > start:
            ranges[slot] = (start, end)
    return ranges


def _build_t8_media(media, main_mode: str, length: int):
    """导演台 media 列表 → T8 build_conditioning 的 refs 参数组。"""
    trim_ranges = _audio_trim_ranges(media)
    effective_duration = max(1, int(length)) / 24.0

    ref_images = {}
    for index in range(1, 10):
        name = _slot_filename(media.get(f"ref_image_{index}") or {})
        if not name:
            continue
        ref_images[f"ref_image_{index}"] = _load_image(name)

    ref_videos, ref_video_audios = {}, {}
    for index in range(1, 4):
        entry = media.get(f"ref_video_{index}") or {}
        name = _slot_filename(entry)
        if not name:
            continue
        slot = f"ref_video_{index}"
        frames, soundtrack = _load_video(name, int(length))
        if frames is None or frames.shape[0] < 1:
            raise ValueError(f"[Deciia 参考引导 (GH→T8 桥)] 参考视频解码为空：{name}")
        ref_videos[slot] = frames
        if soundtrack is not None and not bool(entry.get("muted")):
            reference_duration = _align_frame_count_down(int(frames.shape[0])) / 24.0
            if reference_duration > 0:
                ref_video_audios[f"ref_video_audio_{index}"] = _trim_audio_to_duration(
                    soundtrack, reference_duration
                )

    ref_audios = {}
    for index in range(1, 4):
        name = _slot_filename(media.get(f"ref_audio_{index}") or {})
        if not name:
            continue
        audio = _apply_audio_selection(_load_audio(name), trim_ranges.get(f"ref_audio_{index}"))
        ref_audios[f"ref_audio_{index}"] = _trim_audio_to_duration(audio, effective_duration)

    first_frame = last_frame = None
    drive_audio = None
    hybrid_name = _slot_filename(media.get("hybrid_audio") or {})
    hybrid_audio = _apply_audio_selection(
        _load_audio(hybrid_name), trim_ranges.get("hybrid_audio")
    ) if hybrid_name else None
    hybrid_audio = _trim_audio_to_duration(hybrid_audio, effective_duration)

    if main_mode == "text_keyframes":
        # 关键帧模式：首/尾帧是真正的锚点图，hybrid 音频是内部驱动轨。
        for slot in ("first_frame", "last_frame"):
            name = _slot_filename(media.get(slot) or {})
            if not name:
                continue
            tensor = _load_image(name)
            if slot == "first_frame":
                first_frame = tensor
            else:
                last_frame = tensor
        drive_audio = hybrid_audio
    else:
        # 全参考模式：hybrid 槽同样作为音色参考传入（与导演台语义一致）。
        if hybrid_audio is not None:
            ref_audios.setdefault("ref_audio_1", hybrid_audio)

    return {
        "ref_images": ref_images or None,
        "ref_videos": ref_videos or None,
        "ref_video_audios": ref_video_audios or None,
        "ref_audios": ref_audios or None,
        "first_frame": first_frame,
        "last_frame": last_frame,
        "drive_audio": drive_audio,
        "media_count": (
            len(ref_images), len(ref_videos), len(ref_audios)
        ),
    }


# ---------------------------------------------------------------------- 节点本体
class MiniMaxH3GHGuideT8(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MiniMaxH3GHGuideT8",
            display_name="Deciia 参考引导 (GH→T8 桥)",
            description=(
                "把「MiniMax-H3 智能一体化(GH)」导演台的素材/提示词/尺寸状态，用 T8 的 "
                "build_conditioning 重新编码成条件，输出与 MiniMaxH3AudioConditioningT8 完全一致的 "
                "6 个槽位。双采工作流里放两个实例（一采不接宽高、二采接 upscaler 宽高）即可替代两个 "
                "AudioConditioningT8，素材仍只在导演台面板维护一处。"
            ),
            category="Deciia/T8桥",
            inputs=[
                IntegrationBundle.Input(
                    "integration", display_name="导演台(智能一体化GH)",
                    tooltip="接「MiniMax-H3 智能一体化(GH)」的 > 输出；本节点据此读取导演台素材面板与尺寸。",
                ),
                io.Clip.Input("clip", display_name="CLIP", tooltip="T8 条件编码用的 CLIP（导演台 bundle 里不含 CLIP）。"),
                io.Int.Input(
                    "width", display_name="画布宽(覆盖)", default=0, min=0, max=8192, step=32, optional=True,
                    tooltip="0 = 跟随导演台；二采接 upscaler 的 width 输出。",
                ),
                io.Int.Input(
                    "height", display_name="画布高(覆盖)", default=0, min=0, max=8192, step=32, optional=True,
                    tooltip="0 = 跟随导演台；二采接 upscaler 的 height 输出。",
                ),
                io.Int.Input(
                    "length", display_name="帧数(覆盖)", default=0, min=0, max=3600, step=1, optional=True,
                    tooltip="0 = 跟随导演台时长；需精确控帧时接帧数来源（17n+5 网格）。",
                ),
                io.String.Input(
                    "prompt_override", display_name="提示词(覆盖)", optional=True, force_input=True,
                    tooltip="接了就用它当提示词；不接则用导演台面板里当前模式的提示词。",
                ),
                io.Combo.Input(
                    "task_type", display_name="任务类型",
                    options=["auto", "T2VA", "I2VA", "FL2VA", "L2VA", "Ref2VA", "Hybrid"],
                    default="Ref2VA",
                ),
                io.Combo.Input(
                    "audio_mode", display_name="音频模式",
                    options=["native", "reference_only", "lock_source", "remix_source"],
                    default="native",
                    tooltip="native=模型自由生成；lock/remix 需要驱动音频（关键帧模式取导演台 hybrid 槽）。",
                ),
                io.Combo.Input(
                    "ref_image_size", display_name="参考图尺寸策略", options=["match", "max"], default="match",
                    advanced=True,
                ),
                io.Combo.Input(
                    "reference_video_policy", display_name="参考视频策略",
                    options=["official_2_to_15s", "model_minimum"], default="official_2_to_15s",
                    advanced=True,
                ),
            ],
            hidden=[io.Hidden.prompt, io.Hidden.extra_pnginfo],
            outputs=[
                io.Conditioning.Output(display_name="正向条件"),
                io.Latent.Output(display_name="音视频潜变量"),
                io.Audio.Output(display_name="混音音频"),
                io.String.Output(display_name="处理后提示词"),
                io.String.Output(display_name="媒体映射JSON"),
                io.String.Output(display_name="报告"),
                # 追加端点（第 7~10 槽，前 6 槽编号不变，既有接线零改）：
                # 与「MiniMax-H3 适配器(GH)」同名同类型，取自导演台 bundle，
                # 便于本节点在单采链里直接当适配器替代品使用。
                io.Vae.Output(display_name="视频VAE"),
                io.Vae.Output(display_name="音频VAE"),
                io.Int.Output(display_name="模型序号"),
                io.Boolean.Output(display_name="是否原声"),
            ],
        )

    @classmethod
    def execute(cls, integration=None, clip=None, width=0, height=0, length=0,
                prompt_override=None, task_type="Ref2VA", audio_mode="native",
                ref_image_size="match", reference_video_policy="official_2_to_15s"):
        if not isinstance(integration, dict):
            raise ValueError(
                "[Deciia 参考引导 (GH→T8 桥)] integration 输入为空："
                "请接「MiniMax-H3 智能一体化(GH)」的 > 输出。"
            )

        state_json, node_id = _resolve_media_state(cls)
        state, media = _parse_state(state_json)
        main_mode = state.get("mode") if state.get("mode") in {"text_keyframes", "all_reference"} \
            else integration.get("resolved_mode", "all_reference")

        if integration.get("video_vae") is None or integration.get("audio_vae") is None:
            raise ValueError(
                "[Deciia 参考引导 (GH→T8 桥)] integration bundle 缺少视频/音频 VAE："
                "请确认上游接的是「MiniMax-H3 智能一体化(GH)」的输出。"
            )

        canvas_w = int(width) if width else int(integration.get("width") or 0)
        canvas_h = int(height) if height else int(integration.get("height") or 0)
        frames = int(length) if length else int(integration.get("length") or 0)
        if canvas_w <= 0 or canvas_h <= 0:
            raise ValueError(
                f"[Deciia 参考引导 (GH→T8 桥)] 画布尺寸无效：宽={canvas_w} 高={canvas_h}；"
                "请检查导演台尺寸设置或本节点的宽高覆盖输入。"
            )
        if frames <= 0:
            raise ValueError(
                f"[Deciia 参考引导 (GH→T8 桥)] 帧数无效：{frames}；"
                "请检查导演台时长或本节点的帧数覆盖输入。"
            )

        prompt = (prompt_override or "").strip() or _state_prompt(state, main_mode)
        if not prompt:
            raise ValueError(
                "[Deciia 参考引导 (GH→T8 桥)] 提示词为空：导演台面板该模式下没有提示词，"
                "且本节点的提示词覆盖口未接。"
            )

        built = _build_t8_media(media, main_mode, frames)
        mode = str(audio_mode or "native").lower()
        if mode != "native" and built["drive_audio"] is None:
            raise ValueError(
                f"[Deciia 参考引导 (GH→T8 桥)] 音频模式 {audio_mode} 需要驱动音频："
                "关键帧模式请在导演台挂 hybrid 音频，或改用 native。"
            )

        log.info(
            "[DeciiaGHT8Bridge] 导演台节点=%s 模式=%s 画布=%dx%d 帧=%d 参考图=%d 视频=%d 音频=%d",
            node_id, main_mode, canvas_w, canvas_h, frames, *built["media_count"],
        )

        cond_mod = _load_t8_conditioning()
        result = cond_mod.build_conditioning(
            clip, integration.get("video_vae"), integration.get("audio_vae"),
            prompt, canvas_w, canvas_h, frames,
            task_type=task_type,
            audio_mode=mode,
            audio_denoise_strength=1.0,   # 与 P8a 现役 AudioConditioningT8 取值一致；仅 remix_source 生效
            add_source_as_reference=False,
            prompt_primary_audio_ordinal=0,
            strict_prompt_tags=True,
            ref_image_size=ref_image_size,
            reference_video_policy=reference_video_policy,
            drive_audio=built["drive_audio"],
            final_audio=None,
            first_frame=built["first_frame"],
            last_frame=built["last_frame"],
            ref_images=built["ref_images"],
            ref_videos=built["ref_videos"],
            ref_video_audios=built["ref_video_audios"],
            ref_audios=built["ref_audios"],
            allow_above_reference_area=False,
        )
        # 前 6 槽 = T8 条件契约（与原 AudioConditioningT8 对齐）；
        # 后 4 槽 = 适配器同名端点（VAE/模型序号/是否原声），全部取自导演台 bundle。
        return io.NodeOutput(
            *result[:6],
            integration.get("video_vae"),
            integration.get("audio_vae"),
            _mode_model_number(main_mode),
            bool(integration.get("is_original_audio", False)),
        )


NODE_CLASS_MAPPINGS = {"MiniMaxH3GHGuideT8": MiniMaxH3GHGuideT8}
NODE_DISPLAY_NAME_MAPPINGS = {"MiniMaxH3GHGuideT8": "Deciia 参考引导 (GH→T8 桥)"}

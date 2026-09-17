"""Deciia RefT8Bridge — Ref 导演台 → T8 双采链 桥节点（自建插件，作者标记 deciia）。

本包 = custom_nodes/ComfyUI_Deciia_RefT8Bridge（原 ComfyUI-MiniMaxRef-T8-Bridge，2026-09-11 加 deciia 标记后改名）。

================================================================================
与本插件的关系 / 更新策略
================================================================================
本插件是【独立自定义节点】，不修改、不依赖 MiniMaxRefDirector-ComfyUI 与
comfyui-minimax-h3-audio-T8 的任何源码，二者更新不会破坏本节点注册。

节点语义：
  - 输入 guide_data 来自 MiniMaxRefDirector.guide_data（GUIDE_DATA 为 ComfyUI
    io.Custom 类型，按类型名字符串跨插件匹配，因此无需 import Ref 插件代码）。
  - 条件编码在运行时动态 import 当前安装的 comfyui-minimax-h3-audio-T8 的
    build_conditioning（随 T8 更新自动使用新实现）。
  - 输出槽位与 MiniMaxH3AudioConditioningT8 完全一致（positive/av_latent/
    mux_audio/conditioned_prompt/media_map_json/report），可直接替换 V2 链
    中任意一个 AudioConditioningT8，或作为新的条件源接入 T8 双采采样链。

更新策略判断（供维护者参考）：
  1. MiniMaxRefDirector 更新：
     - 若 guide_data 仍是 dict{width,height,frame_rate,timeline_data:[{prompt,
       durationFrames,images,...}]} 结构 → 本插件无需改动。
     - 若 timeline 段字段改名/嵌套变化 → 只需改本插件的 _extract_* 解析层。
  2. comfyui-minimax-h3-audio-T8 更新：
     - build_conditioning 签名不变 → 本插件零改动（自动跟随）。
     - 若签名变化 → 运行时报错会指明缺失参数，按报错更新 execute 的调用即可。
  3. 图片加载使用 ComfyUI 官方 folder_paths.get_annotated_filepath（与 LoadImage
     同源），兼容 input/ 下任意子目录与绝对路径，不依赖 Ref 的 lib/image.py。

本插件内的其它节点与上述两插件完全无关；删除本插件不影响任何既有工作流。
================================================================================
"""
from __future__ import annotations

import importlib.util
import logging
import os
import sys
from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageOps

import folder_paths
from comfy_api.latest import io

log = logging.getLogger(__name__)

GuideData = io.Custom("GUIDE_DATA")

# ---------------- T8 conditioning 动态加载（目录名含连字符，无法常规 import） ----------------
_T8_COND_MODULE = None
_T8_LOAD_ERROR: str | None = None
_T8_DIRNAME = "comfyui-minimax-h3-audio-T8"
_T8_PKG = "_deciia_t8_cond_pkg"   # 带 deciia 前缀，避免与 T8 插件自身包名撞车


def _find_loaded(py_path) -> object | None:
    """在 sys.modules 里按真实路径找同文件已加载的模块；找到即复用，杜绝同源双份加载。"""
    target = os.path.normcase(os.path.realpath(str(py_path)))
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


def _load_t8_conditioning():
    """返回 T8 的 conditioning 模块。

    1) T8 插件自己已加载同一文件 → 直接复用（类身份一致、无重复内存）；
    2) 未加载时才用 `_deciia_t8_cond_pkg` 合成包兜底加载
       （conditioning.py 内部 `from .core import ...` 依赖包上下文）。
    T8 升级后重启 ComfyUI 即自动使用新代码。
    """
    global _T8_COND_MODULE, _T8_LOAD_ERROR
    if _T8_COND_MODULE is not None:
        return _T8_COND_MODULE
    if _T8_LOAD_ERROR:
        raise RuntimeError(_T8_LOAD_ERROR)

    # T8 自 2026-09-13 起把实现放进 h3_t8/ 子目录（外层 __init__.py 设
    # __path__ = [h3_t8, 根目录]，h3_t8 优先），旧版/内层包也在候选中；
    # 因此按候选目录找 conditioning.py，而不是写死单一路径（09-14 修复）。
    plugin_dir = Path(__file__).resolve().parent.parent / _T8_DIRNAME
    t8_root = None
    for candidate in ("h3_t8", _T8_DIRNAME, "."):
        probe = (plugin_dir / candidate).resolve() if candidate != "." else plugin_dir.resolve()
        if (probe / "conditioning.py").exists():
            t8_root = probe
            break
    if t8_root is None:
        _T8_LOAD_ERROR = (
            f"{_T8_DIRNAME}/**/conditioning.py not found under custom_nodes "
            "(h3_t8/ 与包目录均已查找); this bridge requires it to encode T8 conditioning."
        )
        raise RuntimeError(_T8_LOAD_ERROR)

    loaded = _find_loaded(t8_root / "conditioning.py")
    if loaded is not None and hasattr(loaded, "build_conditioning"):
        _T8_COND_MODULE = loaded
        return loaded

    if _T8_PKG not in sys.modules:
        pkg = type(sys)(_T8_PKG)
        pkg.__path__ = [str(t8_root)]
        sys.modules[_T8_PKG] = pkg
    try:
        for mod in ("core", "prompt_tags", "conditioning"):
            key = f"{_T8_PKG}.{mod}"
            if key in sys.modules:
                continue
            spec = importlib.util.spec_from_file_location(key, t8_root / f"{mod}.py")
            if spec is None or spec.loader is None:
                raise RuntimeError(f"cannot build spec for T8 module {mod}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[key] = module
            spec.loader.exec_module(module)
        _T8_COND_MODULE = sys.modules[f"{_T8_PKG}.conditioning"]
        return _T8_COND_MODULE
    except Exception as exc:  # noqa: BLE001
        _T8_LOAD_ERROR = f"failed to load T8 conditioning: {exc}"
        raise


# ---------------- 参考图加载（ComfyUI 官方路径语义，兼容 LoadImage） ----------------
def _load_image_tensor(filepath: str) -> torch.Tensor | None:
    try:
        p = folder_paths.get_annotated_filepath(filepath)
        with Image.open(p) as im:            # with = 句柄确定性释放（不再依赖 GC）
            img = ImageOps.exif_transpose(im)
            if img.mode == "I":
                img = img.point(lambda v: v * (1 / 255))
            arr = np.array(img.convert("RGB")).astype(np.float32) / 255.0
        return torch.from_numpy(arr)[None, ...]
    except Exception as exc:  # noqa: BLE001
        log.warning("[MiniMaxRefGuideT8] skip image %r: %s", filepath, exc)
        return None


def _extract_picture_paths(images) -> list[str]:
    """GUIDE_DATA 段内 images 元素兼容 {label,src} 字典与纯字符串路径。"""
    out: list[str] = []
    for item in images or []:
        src = item.get("src", "") if isinstance(item, dict) else (item or "")
        if src:
            out.append(str(src))
    return out


def _load_audio_dict(filepath: str) -> dict | None:
    """加载音频文件 → ComfyUI AUDIO dict {waveform, sample_rate}（folder_paths 解析 input/）。"""
    try:
        import av  # PyAV 随 ComfyUI 环境可用（video 解码依赖）
        import torch as _t
    except Exception:
        return None
    try:
        resolved = folder_paths.get_annotated_filepath(str(filepath))
        with av.open(resolved) as af:
            if not af.streams.audio:
                return None
            stream = af.streams.audio[0]
            sr = int(stream.codec_context.sample_rate)
            n_ch = stream.channels
            frames = []
            for frame in af.decode(streams=stream.index):
                buf = _t.from_numpy(frame.to_ndarray())
                if buf.shape[0] != n_ch:
                    buf = buf.view(-1, n_ch).t()
                frames.append(buf)
            if not frames:
                return None
            wav = _t.cat(frames, dim=1)
            if wav.dim() == 1:
                wav = wav.unsqueeze(0)
            if wav.dim() == 2:
                wav = wav.unsqueeze(0)  # [1, N] → [1, 1, N]：T8 validate_audio 要求 [batch, channels, samples]
            return {"waveform": wav.float(), "sample_rate": sr}
    except Exception as exc:  # noqa: BLE001
        log.warning("[MiniMaxRefGuideT8] skip audio %r: %s", filepath, exc)
        return None


def _extract_audio_paths(audios) -> list[str]:
    """GUIDE_DATA 段内 audios：{label,src} 字典或纯字符串路径均可。"""
    return _extract_picture_paths(audios)  # 结构同 images


# ---------------- prev_tail motion-context 衔接（依赖 H3-Motion-Context-MultiRef） ----------------
_MC_GRID = (124, 107, 90, 73, 56, 39, 22, 5, 1)


class _MotionCtxUnavailable(RuntimeError):
    """H3-Motion-Context-MultiRef 插件缺失/不可用 → 调用方回退 first_frame。"""


def _vhs_tuple_path(filenames) -> str:
    """VHS_FILENAMES 元组 → 首个文件绝对路径。

    VHS VideoCombine 的 Filenames 元组结构为 (save_output: bool, files: list)。
    旧实现取 filenames[0] 拿到布尔 save_output，str() 后变成 "True"，
    导致 prev_tail 解码失败（找不到 input/True）。此处优先取 files 列表。
    """
    try:
        if isinstance(filenames, (tuple, list)) and len(filenames) >= 2:
            files = filenames[1]
            if isinstance(files, (list, tuple)) and files:
                # 优先选视频文件（VHS 会把 MetadataImage png 也塞进列表）
                video_exts = (".mp4", ".mkv", ".webm", ".mov", ".avi")
                for first in files:
                    name_str = str(first.get("filename") or first.get("subfolder") or "") if isinstance(first, dict) else str(first)
                    if name_str.lower().endswith(video_exts):
                        if isinstance(first, dict):
                            folder = str(first.get("folder") or "")
                            name = str(first.get("filename") or first.get("subfolder") or "")
                            path = os.path.join(folder, name) if folder else name
                            if path:
                                return path
                        return name_str
                first = files[0]
                if isinstance(first, dict):
                    folder = str(first.get("folder") or "")
                    name = str(first.get("filename") or first.get("subfolder") or "")
                    path = os.path.join(folder, name) if folder else name
                    if path:
                        return path
                elif first:
                    return str(first)
        if isinstance(filenames, (tuple, list)) and filenames:
            first = filenames[0]
            if isinstance(first, dict):
                folder = str(first.get("folder") or "")
                name = str(first.get("filename") or first.get("subfolder") or "")
                return os.path.join(folder, name) if folder else name
            if isinstance(first, bool):
                return ""
            return str(first)
    except Exception:  # noqa: BLE001
        pass
    return str(filenames or "")


def _snap_h3_run(n: int) -> int:
    """向下吸附到合法 H3 run 网格（5/22/39/56...）；<5 → 0。"""
    n = int(n or 0)
    if n < 5:
        return 0
    return ((n - 5) // 17) * 17 + 5


def _load_prev_tail_frames(tail_src: str):
    """解码上段视频尾部 → 帧张量 [N,H,W,C]。用 VHS 的 utils 或 PyAV 兜底。"""
    try:
        from comfy_api.latest import VideoFromFile
        vid = VideoFromFile.load_video(
            video=tail_src, frame_load_cap=-1, start_frame=0, fps_mode="force",
            force_rate=24, skip_first_frames=0, choose_video_source=0, select_every_nth=1,
        )
        img = vid[0]
        if img.ndim == 4:  # [N,H,W,C] 已是 ComfyUI image 约定
            return img
        return None
    except Exception:  # noqa: BLE001
        pass
    try:
        import av
        # 绝对路径直接用；只有相对路径才做 annotated 解析（get_annotated_filepath 对绝对路径会 raise）
        resolved = tail_src if os.path.isabs(tail_src) else folder_paths.get_annotated_filepath(tail_src)
        with av.open(resolved) as af:
            stream = af.streams.video[0]
            if stream.frames and stream.frames > 0:
                start = max(0, int(stream.frames) - 22)
                af.seek(start * stream.time_base.den // stream.time_base.num
                        if stream.time_base else start, any_frame=False)
            frames = []
            for frame in af.decode(streams=stream.index):
                arr = frame.to_ndarray(format="rgb24")  # [H,W,3]
                frames.append(arr)
                if len(frames) >= 22:
                    break
            if not frames:
                return None
            import numpy as np
            batch = np.stack(frames).astype(np.float32) / 255.0  # [N,H,W,3]
            return torch.from_numpy(batch)
    except Exception as exc:  # noqa: BLE001
        log.warning("[MiniMaxRefGuideT8] prev_tail decode failed %r: %s", tail_src, exc)
        return None


def _apply_motion_context_t8(clip, video_vae, audio_vae, prompt, width, height, length,
                             tail_frames, ctx_len, task_type, ref_images, ref_audios, mode):
    """用上段尾帧做 motion-context：先 T8 编码 → 把尾帧作为引导帧叠加回条件/latent。

    返回 (cond_result, latent, trim_frames)。tail_frames 为 [N,H,W,C]。
    """
    import torch as _t
    # 1) 常规 T8 条件编码（与 execute 主路径一致，含 refs）
    cond_mod = _load_t8_conditioning()
    result = cond_mod.build_conditioning(
        clip, video_vae, audio_vae, prompt, width, height, length,
        task_type=task_type, audio_mode=mode, audio_denoise_strength=1.0,
        add_source_as_reference=False, prompt_primary_audio_ordinal=0,
        strict_prompt_tags=True, ref_image_size="match",
        reference_video_policy="official_2_to_15s",
        drive_audio=None, final_audio=None, first_frame=None, last_frame=None,
        ref_images=ref_images, ref_videos=None, ref_video_audios=None,
        ref_audios=ref_audios, allow_above_reference_area=False,
    )
    cond = result[0]
    latent = result[1]
    # 2) motion-context 叠加：把上段尾帧作为 pinned 引导帧写进条件/latent
    try:
        import importlib.util as _ilu
        pkg_name = "ComfyUI-H3-Motion-Context-MultiRef"
        mod = None
        for m in list(sys.modules.values()):
            f = getattr(m, "__file__", "") or ""
            if pkg_name in f.replace("\\", "/") and hasattr(m, "MiniMaxH3MotionContext"):
                mod = m
                break
        if mod is None:
            raise _MotionCtxUnavailable(f"{pkg_name} not installed")
        node = mod.MiniMaxH3MotionContext()
        # 帧数吸附到合法网格且小于 latent 容量
        n = min(ctx_len, tail_frames.shape[0])
        for g in _MC_GRID:
            if g <= n:
                n = g
                break
        if n < 1:
            n = 1
        pinned = tail_frames[:n]  # [N,H,W,C] IMAGE 约定（与节点 context_frames 输入一致）
        # apply() 真实契约（MultiRef nodes.py）：
        #   apply(conditioning, vae, latent, context_frames, context_length,
        #         encode_mode='video', anchor_mode='head', crop='disabled',
        #         audio_context_length=0, audio_mode='timeline', target_start=0,
        #         context_latent=None, audio_vae=None, context_audio=None)
        #   → (CONDITIONING, INT trim_frames)
        out = node.apply(
            conditioning=cond,
            vae=video_vae,
            latent=latent,
            context_frames=pinned,
            context_length=n,
            encode_mode="video",
            anchor_mode="head",
            crop="disabled",
        )
        # 返回 (conditioning, trim_frames) —— 官方节点 RETURN_TYPES
        if isinstance(out, (tuple, list)):
            cond = out[0]
            trim = int(out[1]) if len(out) > 1 else n
        elif isinstance(out, dict):
            cond = out.get("CONDITIONING") or out.get("conditioning") or cond
            trim = int(out.get("trim_frames") or n)
        else:
            trim = n
        # motion context 只改 conditioning + 返回裁帧数；latent 原样（V2 链采样后按 trim 裁）
        return (cond, latent, trim)
    except Exception as exc:  # noqa: BLE001
        log.warning("[MiniMaxRefGuideT8] motion context apply failed, skip: %s", exc)
        return (cond, latent, 0)


class MiniMaxRefGuideT8(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MiniMaxRefGuideT8",
            display_name="Deciia 参考引导 (Ref→T8 桥)",
            description=(
                "Ref 聚合 → T8 条件桥（独立节点）：吃 RefDirector 的 guide_data，"
                "按 guide_index 取段，用 T8 的 build_conditioning 输出与 "
                "MiniMaxH3AudioConditioningT8 完全相同的 6 个槽位，供 V2 双采链直接消费。"
            ),
            category="Deciia/T8桥",
            inputs=[
                GuideData.Input("guide_data", display_name="导演数据", tooltip="MiniMaxRefDirector.guide_data 输出"),
                io.Clip.Input("clip", display_name="CLIP"),
                io.Vae.Input("video_vae", display_name="视频VAE"),
                io.Vae.Input("audio_vae", display_name="音频VAE"),
                io.Int.Input(
                    "guide_index", display_name="镜头序号", default=0, min=0, max=10000, step=1, optional=True,
                    tooltip="timeline 段索引；不连时自动顺序取段。",
                ),
                io.Int.Input(
                    "width", display_name="画布宽(覆盖)", default=0, min=0, max=8192, step=32, optional=True,
                    tooltip="可选覆盖画布宽（默认用 guide_data.width）。一采/二采不同分辨率时接 ResolutionSelector 或 upscaler 输出。",
                ),
                io.Int.Input(
                    "height", display_name="画布高(覆盖)", default=0, min=0, max=8192, step=32, optional=True,
                    tooltip="可选覆盖画布高（默认用 guide_data.height）。",
                ),
                io.Int.Input(
                    "length", display_name="帧数(覆盖)", default=0, min=0, max=3600, step=17, optional=True,
                    tooltip="可选覆盖帧数（默认用段 durationFrames）。",
                ),
                io.Audio.Input(
                    "external_audio", display_name="外部音频(可选)", optional=True,
                    tooltip="可选外部驱动音频；默认 None = native 生成。",
                ),
                io.Custom("VHS_FILENAMES").Input(
                    "prev_tail", display_name="上段视频(衔接)", optional=True,
                    tooltip="forLoop 循环时接上段 VHS 输出；首段或单段不接。启用 motion-context 帧衔接（需 H3-Motion-Context-MultiRef）。",
                ),
                io.Combo.Input(
                    "audio_mode", display_name="音频模式",
                    options=["auto", "native", "reference_only", "lock_source", "remix_source"],
                    default="auto",
                    tooltip="auto=按段数据自动决定：段有 audios 走 ref 参考、guide_data 有 audio_segments 走音轨保护、否则 native 自由生成。手动值强制覆盖。",
                ),
                io.Combo.Input(
                    "task_type", display_name="任务类型",
                    options=["auto", "T2VA", "I2VA", "FL2VA", "L2VA", "Ref2VA", "Hybrid"],
                    default="Ref2VA",
                ),
                io.Combo.Input(
                    "ref_image_size", display_name="参考图尺寸策略", options=["match", "max"], default="match", advanced=True,
                ),
            ],
            outputs=[
                io.Conditioning.Output(display_name="正向条件"),
                io.Latent.Output(display_name="音视频潜变量"),
                io.Audio.Output(display_name="混音音频"),
                io.String.Output(display_name="处理后提示词"),
                io.String.Output(display_name="媒体映射JSON"),
                io.String.Output(display_name="报告"),
                io.Int.Output(display_name="裁剪帧数"),
            ],
        )

    @classmethod
    def execute(cls, guide_data=None, clip=None, video_vae=None, audio_vae=None,
                guide_index=None, width=0, height=0, length=0, external_audio=None,
                prev_tail=None, audio_mode="auto", task_type="Ref2VA",
                ref_image_size="match") -> io.NodeOutput:
        # 越界通知轮 / 调度时序下 guide_data 可能未就位（None 或空 dict）。
        # 与 V3.1 官方 Guide 同语义：静默阻断本轮，让 forLoop 正常收尾，不抛异常。
        if not isinstance(guide_data, dict) or not guide_data.get("timeline_data"):
            from comfy_execution.graph import ExecutionBlocker
            blocker = ExecutionBlocker(None)
            return io.NodeOutput(*(blocker,) * 7)

        timeline = guide_data["timeline_data"]
        idx = int(guide_index) if guide_index is not None else 0
        if idx >= len(timeline):
            from comfy_execution.graph import ExecutionBlocker
            blocker = ExecutionBlocker(None)
            return io.NodeOutput(*(blocker,) * 7)

        # ---- 资源更新通知：把上段视频追加到 Director 前端素材条 ----
        # 与官方 MiniMaxRefGuide 同语义：prev_tail 有值时发 add_material，
        # 前端 Director 组件按 director_node_id 精确过滤后显示在节点底部素材条。
        _director_id = guide_data.get("_director_node_id") if isinstance(guide_data, dict) else None
        if prev_tail:
            try:
                from server import PromptServer
            except ImportError:
                from comfy_api.latest import server as _comfy_server
                PromptServer = _comfy_server.PromptServer
            try:
                payload = {
                    "status": "add_material",
                    "type": "video",
                    "imageFile": prev_tail,
                    "director_node_id": _director_id,
                }
                PromptServer.instance.send_sync("minimax_ref_video_progress", payload)
            except Exception:
                pass  # 通知失败不影响生成

        entry = timeline[idx]
        prompt = entry.get("prompt", "")
        # 外部 length/width/height 覆盖优先；否则用 guide_data 画布 + 段时长
        if length <= 0:
            length = int(entry.get("durationFrames", 0))
        if length <= 0:
            raise ValueError(f"[MiniMaxRefGuideT8] segment {idx} invalid durationFrames={length}.")
        width = int(width) if width else int(guide_data.get("width", 1344))
        height = int(height) if height else int(guide_data.get("height", 768))

        # ---- 段内参考图 → autogrow dict（与 AudioConditioningT8 ref_images 同构）----
        pic_paths = _extract_picture_paths(entry.get("images"))
        ref_images = {}
        for i, path in enumerate(pic_paths, 1):
            if i > 9:
                break
            img = _load_image_tensor(path)
            if img is None:
                continue
            ref_images[f"ref_image_{i}"] = img
        ref_images = ref_images or None

        # ---- 段内 audios（人物参考音色）→ ref_audios（最多 3，T8 原生支持）----
        audio_paths = _extract_audio_paths(entry.get("audios"))
        ref_audios = {}
        for i, path in enumerate(audio_paths, 1):
            if i > 3:
                break
            aud = _load_audio_dict(path)
            if aud is None:
                continue
            ref_audios[f"ref_audio_{i}"] = aud
        ref_audios = ref_audios or None

        # ---- audio_mode 决策：auto = 按段数据自动路由 ----
        mode = str(audio_mode or "auto").lower()
        audio_segments = guide_data.get("audio_segments") or []
        has_audio_segments = bool(audio_segments)
        if mode == "auto":
            # 段有参考音色 → ref_audios 已自动传入（独立通道，与 mode 无关）；
            # audio_mode 语义仅作用于 drive_audio，auto 不接 drive_audio → native 生成氛围音。
            mode = "native"

        # ---- prev_tail（forLoop 上段视频）→ 段间衔接 ----
        # 首选 motion-context 多帧衔接（guideStrength 决定 pinned 帧数 5/22/39...，
        # 需 H3-Motion-Context-MultiRef）；插件缺失或失败时回退 first_frame 单帧锚定。
        # 输出 trim_frames = 解码后需裁掉的引导帧数（接 SegTrim 节点，画面+音频同步裁）。
        trim_frames = 0
        first_frame = None
        if prev_tail is not None:
            try:
                tail_src = prev_tail
                if isinstance(tail_src, (tuple, list)):  # VHS_FILENAMES 元组
                    tail_src = _vhs_tuple_path(prev_tail)
                frames = _load_prev_tail_frames(tail_src)
            except Exception as exc:  # noqa: BLE001
                log.warning("[MiniMaxRefGuideT8] prev_tail decode failed: %s", exc)
                frames = None
            if frames is not None and frames.shape[0] >= 1:
                gs = int(entry.get("guideStrength") or 22)
                ctx_len = _snap_h3_run(gs)
                if ctx_len >= 5:
                    try:
                        res, _lat, trim_frames = _apply_motion_context_t8(
                            clip, video_vae, audio_vae, prompt, width, height, length,
                            frames, ctx_len, task_type, ref_images, ref_audios, mode,
                        )
                        log.info("[MiniMaxRefGuideT8] seg %d motion context %d frames trim=%d",
                                 idx, ctx_len, trim_frames)
                        return io.NodeOutput(*res, trim_frames)
                    except _MotionCtxUnavailable as exc:
                        log.warning("[MiniMaxRefGuideT8] %s; fallback to first_frame", exc)
                    except Exception as exc:  # noqa: BLE001
                        log.warning("[MiniMaxRefGuideT8] motion context failed (%s); fallback to first_frame", exc)
                # 回退：单帧锚定（帧 0 复制上段尾帧，解码后裁 1 帧）
                first_frame = frames[-1:].contiguous()
                trim_frames = 1
                log.info("[MiniMaxRefGuideT8] seg %d first_frame anchor (trim=1)", idx)

        cond = _load_t8_conditioning()
        # 参数对齐 V2 原 AudioConditioningT8（node7/14）显式值：
        #   audio_mode=native, add_source=False, primary_ordinal=0,
        #   strict=True, ref_image_size=match, video_policy=official_2_to_15s,
        #   allow_above_reference_area=False（V2 值；不放开超参考区）
        result = cond.build_conditioning(
            clip, video_vae, audio_vae, prompt, width, height, length,
            task_type=task_type,
            audio_mode=mode,
            audio_denoise_strength=1.0,
            add_source_as_reference=(external_audio is not None),
            prompt_primary_audio_ordinal=0,
            strict_prompt_tags=True,
            ref_image_size=ref_image_size,
            reference_video_policy="official_2_to_15s",
            drive_audio=external_audio,
            final_audio=None,
            first_frame=first_frame,
            last_frame=None,
            ref_images=ref_images,
            ref_videos=None,
            ref_video_audios=None,
            ref_audios=ref_audios,
            allow_above_reference_area=False,
        )
        return io.NodeOutput(*result, trim_frames)


class MiniMaxRefSegTrimT8(io.ComfyNode):
    """裁掉 motion-context 引导帧：画面(IMAGE)与音频(AUDIO)按帧数同步裁头。

    接桥的「裁剪帧数」输出；帧0 通常是上段尾帧的复制/引导帧，成片应从新画面开始。
    """

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MiniMaxRefSegTrimT8",
            display_name="Deciia 段落裁帧 (T8)",
            category="Deciia/T8桥",
            inputs=[
                io.Image.Input("images", display_name="画面"),
                io.Audio.Input("audio", display_name="音频", optional=True),
                io.Int.Input("trim_frames", display_name="裁剪帧数", default=0, min=0, max=512, step=1,
                             tooltip="桥的「裁剪帧数」输出；0=不裁。画面裁头 N 帧，音频按 24fps 裁头 N/24 秒。"),
                io.Float.Input("fps", display_name="帧率", default=24.0, min=1.0, max=120.0, step=0.1),
            ],
            outputs=[
                io.Image.Output(display_name="画面"),
                io.Audio.Output(display_name="音频"),
            ],
        )

    @classmethod
    def execute(cls, images, audio=None, trim_frames=0, fps=24.0):
        trim_frames = int(trim_frames or 0)
        if trim_frames > 0 and images is not None and images.shape[0] > trim_frames:
            images = images[trim_frames:]
        if trim_frames > 0 and audio is not None:
            import torch as _t
            sr = int(audio.get("sample_rate", 24000))
            wav = audio["waveform"]  # [B, C, L]
            cut = int(round(trim_frames / float(fps or 24.0) * sr))
            if 0 < cut < wav.shape[-1]:
                audio = {"waveform": wav[..., cut:], "sample_rate": sr}
        return io.NodeOutput(images, audio)


NODE_CLASS_MAPPINGS = {
    "MiniMaxRefGuideT8": MiniMaxRefGuideT8,
    "MiniMaxRefSegTrimT8": MiniMaxRefSegTrimT8,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "MiniMaxRefGuideT8": "Deciia 参考引导 (Ref→T8 桥)",
    "MiniMaxRefSegTrimT8": "Deciia 段落裁帧 (T8)",
}

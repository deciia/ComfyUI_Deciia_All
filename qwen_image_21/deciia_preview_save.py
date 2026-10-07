"""Qwen-Image 2.1 域节点：Deciia 预览保存。

设计动机：𝙆 Custom Save Image 没有输出口——"仅预览"时图像无法传给下游节点。
本节点 = 仅预览切换 + 文件名前缀 + 格式选择三项常显，其余参数折叠进
"显示高级输入"；输出图像（IMAGE）始终透传，仅预览时不落盘只发 UI 预览。
保存逻辑复用 kaytool CustomSaveImage（ICC 配置/元数据/精确文件名/时间戳前缀），
kaytool 缺失或 webp 格式时走内置兜底。
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime

import numpy as np
from PIL import Image, ImageCms
from PIL.PngImagePlugin import PngInfo

import folder_paths

try:  # kaytool 可选依赖
    from kaytool.nodes.custom_save_image import CustomSaveImage
except Exception:
    CustomSaveImage = None


def _pnginfo_metadata(prompt, extra_pnginfo) -> PngInfo:
    metadata = PngInfo()
    if prompt is not None:
        metadata.add_text("prompt", json.dumps(prompt))
    if extra_pnginfo:
        for k, v in extra_pnginfo.items():
            metadata.add_text(k, json.dumps(v))
    return metadata


class DeciiaPreviewSaveImage:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "仅预览": ("BOOLEAN", {"default": True,
                                       "tooltip": "开启：不写 output 目录，仅界面预览 + 图像透传下游。"}),
                "图像": ("IMAGE",),
                "文件名前缀": ("STRING", {"default": "Deciia",
                                          "tooltip": "支持 %date:yyyy-MM-dd% 时间戳占位符。"}),
                "格式": ("COMFY_DYNAMICCOMBO_V3", {
                    "tooltip": "输出格式；质量/压缩等子选项随格式在『显示高级输入』展开。",
                    "options": [
                        {"key": "png", "inputs": {"required": {
                            "png_compress": ("INT", {"advanced": True, "default": 4, "min": 0, "max": 9,
                                                     "tooltip": "PNG 压缩等级：数字越大文件越小、保存越慢。"}),
                        }}},
                        {"key": "jpg", "inputs": {"required": {
                            "质量": ("INT", {"advanced": True, "default": 95, "min": 1, "max": 100}),
                        }}},
                        {"key": "webp", "inputs": {"required": {
                            "质量": ("INT", {"advanced": True, "default": 90, "min": 1, "max": 100}),
                        }}},
                    ],
                }),
                # —— 以下折叠进「显示高级输入」（同官方“保存图像（高级）”交互） ——
                "色彩配置": (["sRGB IEC61966-2.1", "Adobe RGB (1998)"],
                              {"advanced": True, "default": "sRGB IEC61966-2.1"}),
                "保存元数据": ("BOOLEAN", {"advanced": True, "default": True,
                                            "tooltip": "写入 prompt/工作流 PNG 元数据。"}),
                "作者": ("STRING", {"advanced": True, "default": ""}),
                "版权信息": ("STRING", {"advanced": True, "default": ""}),
                "精确文件名": ("BOOLEAN", {"advanced": True, "default": False,
                                           "tooltip": "把文件名前缀当作完整文件名，不追加时间戳。"}),
            },
            "hidden": {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO"},
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("图像",)
    FUNCTION = "save"
    OUTPUT_NODE = True
    CATEGORY = "Deciia/Qwen-Image"
    DESCRIPTION = "Deciia 预览保存：仅预览/保存切换 + 图像透传（补 𝙆 节点无输出口）"

    # dynamiccombo 传参形状（1.54.7 内核实测）：
    #   {"format": "jpg", "质量": 88, ...}  子参数平铺在同一个 dict 里
    def save(self, 仅预览, 图像, 文件名前缀, 格式, 色彩配置, 保存元数据, 作者, 版权信息, 精确文件名,
             prompt=None, extra_pnginfo=None, **fmt_extra):
        fdict = 格式 if isinstance(格式, dict) else {}
        merged = {**fmt_extra, **{k: v for k, v in fdict.items() if k != "format"}}
        fmt = str(fdict.get("format") or fmt_extra.get("format") or "png").lower()
        try:
            quality = int(merged.get("质量") or 95)
        except (TypeError, ValueError):
            quality = 95
        try:
            compress = int(merged.get("png_compress") or 4)
        except (TypeError, ValueError):
            compress = 4

        images = 图像
        stamp = int(time.time() * 1000)

        # —— 1) UI 预览：始终发（temp 目录 PNG），前端才有图看 ——
        results = []
        temp_dir = folder_paths.get_temp_directory()
        os.makedirs(temp_dir, exist_ok=True)
        metadata = _pnginfo_metadata(prompt, extra_pnginfo) if 保存元数据 else None
        for idx, image in enumerate(images):
            arr = np.clip(255.0 * image.cpu().numpy(), 0, 255).astype(np.uint8)
            img = Image.fromarray(arr)
            if img.mode != "RGB":
                img = img.convert("RGB")
            name = f"deciia_preview_{stamp}_{idx:03d}.png"
            img.save(os.path.join(temp_dir, name), compress_level=compress, pnginfo=metadata)
            results.append({"filename": name, "subfolder": "", "type": "temp"})

        # —— 2) 正式保存（仅预览=False）：优先 kaytool，缺它或 webp 走兜底 ——
        if not 仅预览:
            # kaytool 的 temp 交换文件路径含前缀子目录但只建 temp 根（如 "lazy/日期/z"），
            # 子目录不存在会 FileNotFoundError —— 这里按同样的日期规则预建目录。
            _prefix_raw = str(文件名前缀 or "")
            _dirpart = _prefix_raw.replace("\\", "/").rsplit("/", 1)[0] if "/" in _prefix_raw.replace("\\", "/") else ""
            if _dirpart:
                def _dt_sub(m2):
                    f2 = (m2.group(2) or "").replace("yyyy", "%Y").replace("MM", "%m").replace("dd", "%d").replace("HH", "%H").replace("mm", "%M").replace("ss", "%S")
                    import datetime as _dt
                    try:
                        return _dt.datetime.now().strftime(f2)
                    except Exception:
                        return m2.group(0)
                _dirpart = re.sub(r"%(date|time):([^%]*)%", _dt_sub, _dirpart)
                _safe = os.path.join(folder_paths.get_temp_directory(), *_dirpart.split("/"))
                os.makedirs(_safe, exist_ok=True)
                # kaytool 正式保存的 final 路径同样把前缀子目录拼进 output/Custom_Save_Image/
                # （shutil.copy2 不建目录 → WinError 3），一并预建
                _safe_out = os.path.join(
                    folder_paths.get_output_directory(), "Custom_Save_Image", *_dirpart.split("/")
                )
                os.makedirs(_safe_out, exist_ok=True)
            fmt_up = {"png": "PNG", "jpg": "JPG"}.get(fmt)
            if CustomSaveImage is not None and fmt_up:
                saved = CustomSaveImage().save_images(
                    images=images,
                    preview_only=False,
                    filename_prefix=文件名前缀 or "Deciia",
                    save_metadata=保存元数据,
                    format=fmt_up,
                    jpg_quality=quality,
                    author=作者 or "",
                    copyright_info=版权信息 or "",
                    color_profile=色彩配置 or "sRGB IEC61966-2.1",
                    exact_filename=精确文件名,
                    prompt=prompt,
                    extra_pnginfo=extra_pnginfo,
                )
                ui_imgs = ((saved or {}).get("ui") or {}).get("images") or []
                if ui_imgs:
                    # kaytool 的 temp 条目 subfolder 报 ""，但文件实际落在前缀子目录
                    # （如 lazy/2026-10-03/）→ 前端 /view 404、节点上不显示图。
                    # 按 dirpart 预建规则探测真实落点，补上 subfolder：
                    _fixed = []
                    for _it in ui_imgs:
                        _fn = str(_it.get("filename") or "").replace("\\", "/")
                        if _fn and "/" in _fn and not str(_it.get("subfolder") or ""):
                            # kaytool 有时把前缀子目录直接拼进 filename
                            # （如 lazy/2026-10-03/z_temp_x.png），subfolder 报空。
                            # /view 只在根下找 → 404。拆开：目录给 subfolder，纯文件名给 filename。
                            _sf, _, _base = _fn.rpartition("/")
                            _root = folder_paths.get_temp_directory() if str(_it.get("type") or "") == "temp" else folder_paths.get_output_directory()
                            # 文件可能躺在根下（filename含整条路径），也可能在拆分后的子目录
                            _ok = os.path.isfile(os.path.join(_root, _fn)) or os.path.isfile(os.path.join(_root, _sf, _base))
                            if _ok:
                                _it = dict(_it, filename=_base, subfolder=_sf)
                        _fixed.append(_it)
                    results = _fixed  # 已保存 → 预览指向真实文件
            else:
                results = self._fallback_save(images, 文件名前缀 or "Deciia", fmt, quality,
                                              metadata, compress)

        # 'ui' 与 'result' 在本内核 execution.py 中为两组独立判断，可共存
        return {"ui": {"images": results}, "result": (images,)}

    def _fallback_save(self, images, prefix, fmt, quality, metadata, compress):
        out_dir = os.path.join(folder_paths.get_output_directory(), "Deciia")
        os.makedirs(out_dir, exist_ok=True)
        items = []
        for idx, image in enumerate(images):
            arr = np.clip(255.0 * image.cpu().numpy(), 0, 255).astype(np.uint8)
            img = Image.fromarray(arr)
            if img.mode != "RGB":
                img = img.convert("RGB")
            name = f"{prefix}_{int(time.time() * 1000)}_{idx:03d}.{fmt}"
            path = os.path.join(out_dir, name)
            if fmt in ("jpg", "webp"):
                img.save(path, quality=quality)
            else:
                img.save(path, compress_level=compress, pnginfo=metadata)
            items.append({"filename": name, "subfolder": "Deciia", "type": "output"})
        return items


NODE_CLASS_MAPPINGS = {"DeciiaPreviewSaveImage": DeciiaPreviewSaveImage}
NODE_DISPLAY_NAME_MAPPINGS = {"DeciiaPreviewSaveImage": "Deciia 预览保存"}

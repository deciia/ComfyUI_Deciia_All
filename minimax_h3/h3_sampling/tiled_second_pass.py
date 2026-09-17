"""
Deciia 分块二采执行器（现居 ComfyUI_Deciia_All/minimax_h3/h3_sampling）

= GH 逐步同步分块算法（Goohai-MiniMax-H3_Integration 的 tiled_sampler）的 deciia 调用壳。
不复制算法、不预校验 hybrid 契约（该预校验假设核心 PackedLayout 支持 frame_count，旧核心会 TypeError），
直接调 GH 的 TiledSamplerLegacy.sample_tiled，规避上游版本错配。

模块加载策略（2026-09-11 规范化）：
  1. 若 GH 插件自己已经把 tiled_sampler.py 加载进内存 → **直接复用**，避免同一份源码被加载两次
     （双份模块 = 内存翻倍 + 类身份不一致）；
  2. 未加载时才用 deciia 前缀的合成包（目录名含连字符，无法常规 import）按需加载；
  3. 合成包名带下划线前缀 `_deciia_gh_pkg`，不会与 GH 插件自身的包名撞车。

采样期间的 pack 兼容由 h3_compat / solattn_compat 两个上下文垫片负责（见同包模块）。
"""
from __future__ import annotations

import importlib.util as _ilu
import logging
import os
import sys
import types as _types

from comfy_api.latest import io

log = logging.getLogger(__name__)

_GH_DIRNAME = "Goohai-MiniMax-H3_Integration"
_PKG_NAME = "_deciia_gh_pkg"
_LOAD_ERROR: str | None = None


def _gh_dir() -> str:
    return os.path.normpath(os.path.join(os.path.dirname(__file__), "..", _GH_DIRNAME))


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


def _load_into_pkg(gh_dir: str, mod_name: str):
    """把 <gh_dir>/<mod_name>.py 以 _deciia_gh_pkg.<mod_name> 加载（必要时建合成包）。"""
    key = f"{_PKG_NAME}.{mod_name}"
    if key in sys.modules:
        return sys.modules[key]
    if _PKG_NAME not in sys.modules:
        pkg = _types.ModuleType(_PKG_NAME)
        pkg.__path__ = [gh_dir]
        sys.modules[_PKG_NAME] = pkg
    spec = _ilu.spec_from_file_location(key, os.path.join(gh_dir, f"{mod_name}.py"))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot build import spec for {key}")
    module = _ilu.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


def _get_legacy():
    """返回 GH 的 TiledSamplerLegacy 类（复用优先，合成包兜底）。"""
    global _LOAD_ERROR
    gh_dir = _gh_dir()
    sampler_py = os.path.join(gh_dir, "tiled_sampler.py")
    if not os.path.exists(sampler_py):
        _LOAD_ERROR = f"{_GH_DIRNAME}/tiled_sampler.py not found under custom_nodes"
        raise RuntimeError(_LOAD_ERROR)

    # 1) 复用 GH 插件自己加载的那份
    loaded = _find_loaded(sampler_py)
    if loaded is not None and hasattr(loaded, "_GoohaiMinimaxH3TiledSamplerLegacy"):
        return loaded._GoohaiMinimaxH3TiledSamplerLegacy

    # 2) 合成包加载（tiled_sampler 内部 `from .sampling import ...` 由包的 __path__ 自动解析）
    try:
        mod = _load_into_pkg(gh_dir, "tiled_sampler")
        legacy = mod._GoohaiMinimaxH3TiledSamplerLegacy
    except Exception as exc:  # noqa: BLE001
        _LOAD_ERROR = f"failed to load GH tiled_sampler: {exc}"
        log.error("[Deciia] %s", _LOAD_ERROR)
        raise RuntimeError(_LOAD_ERROR) from exc

    # GH 的 conditioning 与本节点无直接依赖，能加载就加载（同包上下文），失败不影响分块采样
    try:
        if _find_loaded(os.path.join(gh_dir, "conditioning.py")) is None:
            _load_into_pkg(gh_dir, "conditioning")
    except Exception as exc:  # noqa: BLE001
        log.debug("[Deciia] GH conditioning 预加载跳过: %s", exc)
    return legacy


class DeciiaTiledSecondPass(io.ComfyNode):
    """GH 逐步同步分块二采的 deciia 调用壳（不做 hybrid 契约预校验）。"""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="DeciiaTiledSecondPass",
            display_name="Deciia 分块二采执行器(GH同步·无hybrid预校验)",
            category="Deciia/Sampling",
            inputs=[
                io.Noise.Input("noise"),
                io.Guider.Input("guider"),
                io.Sampler.Input("sampler"),
                io.Sigmas.Input("sigmas"),
                io.Latent.Input("latent_image"),
                io.Boolean.Input("enable_tiling", default=True),
                io.Int.Input("n_tiles", default=4, min=1, max=8),
                io.Int.Input("tile_overlap", default=128, min=0, max=512),
            ],
            outputs=[io.Latent.Output("输出"), io.Latent.Output("降噪输出")],
        )

    @classmethod
    def execute(cls, noise, guider, sampler, sigmas, latent_image,
                enable_tiling=True, n_tiles=4, tile_overlap=128):
        legacy_cls = _get_legacy()
        from .h3_compat import packedlayout_frame_count_shim
        from .solattn_compat import solattn_signature_shim
        # 采样期垫片：旧核心剥离 frame_count；comfy_kitchen 新内核参数别名；退出即还原
        with packedlayout_frame_count_shim(), solattn_signature_shim():
            return legacy_cls().sample_tiled(
                noise, guider, sampler, sigmas, latent_image,
                enable_tiling, n_tiles, tile_overlap,
                False, 4,
            )


NODE_CLASS_MAPPINGS = {"DeciiaTiledSecondPass": DeciiaTiledSecondPass}
NODE_DISPLAY_NAME_MAPPINGS = {"DeciiaTiledSecondPass": "Deciia 分块二采执行器(GH同步·无hybrid预校验)"}

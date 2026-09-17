"""
deciia Sol-Attn 签名兼容层（现居 ComfyUI_Deciia_All/minimax_h3/h3_sampling）
针对: sol_attn_minimax_v2(调用方) 传 max_blocks/centroid_tail/reuse_qkv_memory,
comfy_kitchen 新内核无这些参数 → TypeError → 节点每次 fallback 慢速注意力。
映射: max_blocks=N(块数,>0) → topk_ratio=N/ceil(T/64); centroid_tail → tail; reuse_qkv_memory → 剥除。
内核签名运行时自检(_deciia_compat 标记幂等安装), 内核升级自动适配; 不改任何插件源码。
"""
from __future__ import annotations

import functools
import inspect
import logging
import math

log = logging.getLogger(__name__)

_BLOCK = 64  # 调用方 BLOCK_SIZE
_installed = False


def _install_once():
    global _installed
    if _installed:
        return
    import comfy_kitchen as ck
    import comfy_kitchen.backends.cuda as ck_cuda

    def make(inner):
        try:
            sig = set(inspect.signature(inner).parameters)
        except (TypeError, ValueError):
            sig = None

        @functools.wraps(inner)
        def compat(q, k, v, *args, **kwargs):
            if sig is not None and kwargs:
                extra = {kk: vv for kk, vv in kwargs.items() if kk not in sig}
                if extra:
                    for kk in extra:
                        kwargs.pop(kk, None)
                    cap = extra.get("max_blocks") or 0
                    if cap > 0 and "topk_ratio" in sig:
                        try:
                            blocks = math.ceil(q.shape[-2] / _BLOCK)
                            kwargs["topk_ratio"] = min(1.0, cap / max(1, blocks))
                        except Exception:
                            pass
                    if extra.get("centroid_tail") and "tail" in sig:
                        kwargs.setdefault("tail", True)
            return inner(q, k, v, *args, **kwargs)

        compat._deciia_compat = True
        return compat

    for mod in (ck, ck_cuda):
        fn = getattr(mod, "sol_attn", None)
        if fn is not None and not getattr(fn, "_deciia_compat", False):
            setattr(mod, "sol_attn", make(fn))
    _installed = True
    log.info("[Deciia兼容层] Sol-Attn 签名垫片已安装(max_blocks→topk_ratio, centroid_tail→tail)")


def install():
    _install_once()


class solattn_signature_shim:
    def __enter__(self):
        install()
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

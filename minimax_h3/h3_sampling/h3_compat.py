"""
deciia H3 兼容层（现居 ComfyUI_Deciia_All/minimax_h3/h3_sampling）
针对: GH/T8 插件(09-09版)假设核心 PackedLayout.__init__ 支持 frame_count,
而本机 aki 核心(09-05版)无该参 → TypeError。

策略: 类级 monkey-patch —— 对 PackedLayout 类对象本身临时包装 __init__,
剥离 frame_count 参数。对一切 import 风格免疫(模块级/函数级/延迟导入拿到
的都是同一个类对象)。用法 = 上下文管理器, 只在采样期间生效, finally 还原。

语义: 旧核心丢 frame_count = GH 作者在 tiled_sampler._make_packed_layout
自带的兼容分支同款行为, 不偏离作者意图。采样期间 ComfyUI 单任务独占,
窗口内无并发风险; 禁止做成全局永久补丁。
2026-09-11: 增加重入计数保护(嵌套 with 只装/卸一次, 避免链式包装还原不彻底);
输出改 logging。
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

_active = 0


def frame_count_supported() -> bool:
    """核心 PackedLayout.__init__ 是否真正接受 frame_count(试构造探测, 防包装器误判)。

    不能用 inspect.signature: sol_attn 等插件的 wrapper 带 **kwargs,
    会被误判为'原生支持'而跳过垫片 → frame_count 穿透 → TypeError。
    改为对类做无副作用试调: 传 frame_count, 捕 TypeError。
    (真实 PackedLayout 构造有重计算, 这里用 object.__new__ 跳过 __init__ 之外的逻辑,
     直接探测 __init__ 链是否吃该 kwargs; 不触发任何张量操作。)
    """
    from comfy.ldm.minimax.model import PackedLayout
    probe = object.__new__(PackedLayout)
    try:
        PackedLayout.__init__(probe, 0, 0, 0, 0, 0, frame_count=5)
        return True
    except TypeError:
        return False
    except Exception:
        # TypeError 之外的错 = 签名收下了 frame_count, 但后续逻辑因假数据崩 → 视为支持
        return True


class packedlayout_frame_count_shim:
    """上下文管理器: with packedlayout_frame_count_shim(): ...sample_tiled..."""

    def __enter__(self):
        global _active
        from comfy.ldm.minimax.model import PackedLayout
        self._orig = PackedLayout.__init__
        self._patched = False
        if not frame_count_supported() and _active == 0:
            def _compat(self_, *args, **kwargs):
                kwargs.pop("frame_count", None)
                return self._orig(self_, *args, **kwargs)
            PackedLayout.__init__ = _compat
            self._patched = True
            _active += 1
            log.info("[Deciia兼容层] PackedLayout.__init__ 垫片生效(剥离 frame_count, 旧核心)")
        return self

    def __exit__(self, exc_type, exc, tb):
        global _active
        if self._patched:
            from comfy.ldm.minimax.model import PackedLayout
            PackedLayout.__init__ = self._orig
            self._patched = False
            _active = max(0, _active - 1)
            log.info("[Deciia兼容层] PackedLayout.__init__ 已还原")
        return False

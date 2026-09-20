# 节点参考

## minimax_h3/h3_sampling

### DeciiaChunkedPass2SamplerLegacy
H3 二采**时间分块**执行器（T8 `chunked_two_pass_upscale_advanced` 薄壳）。

> 命名说明：T8 v1.85.0 起官方内置同名节点 `DeciiaChunkedPass2Sampler`
> （原生适配实现，安全默认 `preserve_first_pass`）。本包节点已改名加
> `Legacy` 后缀共存、互不遮蔽：工作流里 `DeciiaChunkedPass2Sampler`
> 解析到 T8 官方版，`DeciiaChunkedPass2SamplerLegacy` 解析到本包薄壳。
> 两者 widget 顺序一致，可互换。

- 输入 `noise / guider / sampler / sigmas / latent_image`，输出
  `output / denoised_output`，与 `SamplerCustomAdvanced` 接口同形，
  可 1:1 原位替换（下游 decode 永远接 `output` 口）。
- 目标分辨率自动推导 = 输入 latent ×16（VAE 下采样系数）。
- v4 计划在壳内组装：`full_frame_safe` 空间策略 +
  `guarded_overlap_exp` 时间分块 + `joint_av_preserve_input`。
- v4 schema 自动触发 per-piece sampler rebind（官方机制），
  规避 shape-bound 失败（T8 issue #18）。

**audio_output（音频策略）**：
- `refined_exp`（默认）：执行器运行期间临时拦截 T8 模块级
  `sample_piece`，截获每段本会被丢弃的二采精修音频，按
  `FRAME_RESCALE` 帧号映射的绝对时间区间对位拼回全长缓冲，
  段间重叠区线性交叉渐变；任何异常回退一采原声，不影响视频。
- `preserve_first_pass`：诊断对照，直通一采原声（v4 原行为）。

**提示词覆盖要求**：分镜时间轴必须覆盖**全时长**（含尾块收尾
描述），否则尾块会出现音频幻觉与块边界动作断裂——无描述区间
是块界决策不一致的高发区。

### DeciiaTiledSecondPass
H3 二采**空间分块**执行器（GH `TiledSamplerLegacy.sample_tiled`
薄壳）。与 T8 链不兼容，仅作 GH 链备援。

## stacks

### DeciiaLoraStack
动态 LoRA 串：槽位运行时增删/重排，串内每个 LoRA 四元组
（model+clip+权重）自动向下传递；`bypass / normal / standard`
三种槽位模式。前端面板见 `web/deciia_lora_stack_v12.js`。

### DeciiaVramSafeLoraStack
显存安全变体：加载时逐一检测显存余量，不足的槽位自动旁路并
在报告里标注。前端面板见 `web/deciia_vramsafe_lora_stack.js`。

## minimax_h3/ght8_bridge

### MiniMaxH3GHGuideT8
把 GH 导演台「MiniMax-H3 智能一体化(GH) + 适配器(GH)」的素材
bundle 接入 T8 双采链：一个节点替代一采 LOW / 二采 HIGH 两个
`MiniMaxH3AudioConditioningT8`。自行按目标画布重新编码参考图
（T8 二采要求参考图按目标像素预算缩放），因此能提供宽/高/帧数/
提示词覆写入口——适配器(GH) 本身没有这些入口。

## minimax_h3/reft8_bridge

### MiniMaxRefGuideT8
MiniMaxRefDirector 时间线 → T8 条件编码。输入 `guide_data`
（Ref 导演台聚合输出），输出 6 槽与 `MiniMaxH3AudioConditioningT8`
完全一致（positive / av_latent / mux_audio / conditioned_prompt /
media_map_json / report），可原位替换或作新条件源。

### MiniMaxRefSegTrimT8
Ref 分镜段落裁帧辅助节点。

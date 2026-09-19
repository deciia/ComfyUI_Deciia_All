# Example Workflows

## P8a-T8-H3-ChunkedPASS2-testB.json

GH 全家桶前端 + T8 官方分块二采（`standard_joint_4plus4_exp`）的融合工作流。一采低分辨率出稿，二采时间分块高分辨率精修，**官方路线原生交付二采音频**。

### 两条二采路线（按需解静音切换，另一路保持静音）

| 路线 | 节点组 | 特点 |
|---|---|---|
| 4plus4 官方路线（推荐） | 「4plus4 路线」组：ChunkedTwoPassPlan + ChunkedTwoPassUpscale | 每窗后 4 步联合 AV 重采样，原生二采音频，无补丁依赖 |
| 旧路线（壳 + refined_exp） | 「旧路线」组：DeciiaChunkedPass2Sampler | DetailMixer 精修口（Tail/Bias/STG/Restart），二采音频靠截获拼接 |

注意：GH HIGH 桥（`MiniMaxH3GHGuideT8`）的 width/height 必须与所走路线对齐——官方路线接 Plan 的目标尺寸输出口；旧路线接 LearnedLatentUpscale 的同名牌（否则 Reconcile 报形状不匹配）。

### 节点依赖

**T8 · comfyui-minimax-h3-audio-T8**（[T8mars/comfyui-minimax-h3-audio-T8](https://github.com/T8mars/comfyui-minimax-h3-audio-T8)，建议 v1.84.0+）
- MiniMaxH3DualClockSamplerT8（双时钟采样器）
- SolAttnMiniMax（T8 仓库根目录自带单文件插件 sol_attn_minimax_v2.py，参数 1.3/0.2/0.9/12288/exact_kv_and_rows/2d_frame）
- MiniMaxH3LearnedTwoPassParityPlanT8Advanced（一采/二采步数表，本例 [8,4,4]）
- MiniMaxH3ChunkedTwoPassPlanT8Advanced / MiniMaxH3ChunkedTwoPassUpscaleT8Advanced（官方分块二采，EXP）
- MiniMaxH3LearnedLatentUpscaleT8Advanced / MiniMaxH3TwoPassLatentReconcileT8Advanced / MiniMaxH3TwoPassDetailMixerT8Advanced（旧路线）
- MiniMaxH3AVDecodeT8（音视频联合解码）

**GH · Goohai-MiniMax-H3_Integration**
- MiniMaxH3IntegrationGH（参考图/主体定义/提示词一站式前端）

**Deciia · ComfyUI_Deciia_All**（本仓库）
- MiniMaxH3GHGuideT8（GH→T8 桥，把 GH 前端产物用 T8 build_conditioning 重编码）
- DeciiaVramSafeLoraStack（分档 LoRA 栈，LOW/HIGH 差异化强度）
- DeciiaChunkedPass2Sampler（T8 v4 时间分块二采壳，旧路线用）

**加速与显存**
- MiniMaxH3MemoryEfficientSageAttentionPatch、MiniMaxChunkFeedForward、MiniMaxLowVRAMAttention、ModelAttentionBackend（KJNodes / 内置）

**其它**
- VHS_VideoCombine（VideoHelperSuite，输出 MP4）
- ConcatTextOfUtils（utils-nodes）、CR Prompt Text（ComfyUI_Comfyroll_CustomNodes）、easy anythingIndexSwitch（ComfyUI-Easy-Use，底模 A/B 切换）
- BasicGuider / RandomNoise / SamplerCustomAdvanced / LoraLoaderBypassModelOnly / ModelAttentionBackend / CLIPLoader / UNETLoader / VAELoader（ComfyUI 内置）
- GetNode / SetNode / MarkdownNote / Note（前端内置）

### 模型依赖（models/ 相对路径）

- diffusion_models：DasiwaMinimaxH3_dasiwaHybridV2_int8.safetensors、FeiHou_MiniMax-H3_Remix_v0.6_int8_convrot_v2.safetensors（二选一，IndexSwitch 切换）
- latent_upscale：minimax_h3_latent_upscaler_3d_fp16.safetensors（Plan 与旧路线各引用一次）
- vae：minimax_h3_video_vae_fp16.safetensors、minimax_h3_audio_vae_fp32.safetensors
- clip：qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors
- loras：minimax_h3_fl2v_turbo_4step_v1.2_768p_comfyui_bf16.safetensors（Turbo，必装）；H3_Combat_V2 等动作 LoRA 可选，在 DeciiaVramSafeLoraStack 中开关

### 输入

- 参考图：`双剑女.png`、`长枪男.png`（放 ComfyUI `input/`，或加载后在 GH 节点自行选择）
- 提示词：GH 节点内 integrated_multimodal_description 格式；旧路线二采提示词走 Concat 拼接

### 隐私说明

本示例已清除：API key、本机路径、GH 优化器（optimizer）本机模型缓存、VHS 视频预览元数据。加载后如需 LLM 提示词优化，请在 GH 面板自行配置。

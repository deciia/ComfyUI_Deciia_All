# 上游依赖关系

本仓库与第三方插件的关系全部是**运行时互操作**：动态 import、
类型名字符串匹配、socket 输出对接。**不包含、不修改、不分发**
任何上游插件的代码；上游可独立正常更新，删除本仓库不影响
上游插件与既有工作流。

| 上游插件 | 本仓库使用方 | 互操作方式 | 上游许可 |
|---|---|---|---|
| [comfyui-minimax-h3-audio-T8](https://github.com/T8mars/comfyui-minimax-h3-audio-T8) | h3_sampling（chunked_pass2 / tiled_second_pass）；reft8_bridge（条件编码） | 运行时动态 import 其内部模块（`execute_chunked_two_pass_upscale`、`build_conditioning` 等）：优先按真实路径比对复用 sys.modules 已加载模块，未加载时以合成包名从其安装目录加载 | GPL-3.0-or-later |
| [Goohai-MiniMax-H3_Integration](https://github.com/Goohaitools/Goohai-MiniMax-H3_Integration) | ght8_bridge | 只读其导演台节点的 socket 输出（`io.Custom("MiniMax")` 按类型名匹配）；T8 侧动态 import 同上 | GPL-3.0-or-later |
| [MiniMaxRefDirector-ComfyUI](https://github.com/ktaivla/MiniMaxRefDirector-ComfyUI) | reft8_bridge | 只读其 `guide_data` 输出（`io.Custom GUIDE_DATA` 按类型名匹配，无需 import 其代码） | 见上游仓库 |
| ComfyUI 核心 | h3_sampling 兼容垫片 | 上下文作用域包装 `comfy.ldm.minimax.model.PackedLayout`（签名差异兼容）与 comfy_kitchen Sol-Attn 入口（新旧签名兼容）；进程级幂等，仅采样窗口内生效，`finally` 必还原，非永久补丁 | GPL-3.0 |

## 行为边界

- 所有垫片/monkey-patch 都是**临时、可重入、异常安全**的：
  进入采样时装载，`finally` 还原；嵌套使用有重入计数保护。
- 桥接节点在上游插件缺失时给出明确报错，不静默降级。
- 本仓库节点的输出契约（socket 数量/类型）与被替代的上游节点
  完全一致（如 `MiniMaxRefGuideT8` 对齐 `MiniMaxH3AudioConditioningT8`
  的 6 槽契约），因此可以原位替换、工作流连线无需重排。

## 致谢

- [T8mars](https://github.com/T8mars) 的 comfyui-minimax-h3-audio-T8：
  H3 双采链、分块执行器内核、以及 issue 区高质量的机制讨论。
- Goohaitools 与 MiniMaxRefDirector 作者的导演台/参考工作流节点。

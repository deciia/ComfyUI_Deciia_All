"""Deciia 工作台节点：把工具清单暴露给工作流（可选），并提供侧栏入口。

节点本身轻量：不排队、不加载模型，只把当前工具清单序列化输出，
便于工作流记录/传递；真正的交互在侧栏面板（web/deciia_workstation.js）。
"""

from __future__ import annotations

import json

from comfy_api.latest import io

from .store import load_tools


class DeciiaWorkstation(io.ComfyNode):
    """工作台（启动器）：统一入口打开本机各服务/工具。"""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="DeciiaWorkstation",
            display_name="Deciia 工作台 (Workstation)",
            category="Deciia/UI",
            description=(
                "侧栏「工作台」面板的节点入口。工具条目在工作台面板里增删（支持手动填写、"
                "粘贴一行添加、从剪贴板/JSON 批量导入、探测本机常见端口自动发现），"
                "每条可选择在 ComfyUI 内浮层打开（embed）或浏览器新窗口打开（new_window）。"
                "本节点只输出当前清单 JSON，不排队、不加载模型。"
            ),
            inputs=[],
            outputs=[
                io.String.Output("tools_json"),
                io.Int.Output("tool_count"),
            ],
        )

    @classmethod
    def execute(cls):
        data = load_tools()
        return io.NodeOutput(json.dumps(data["tools"], ensure_ascii=False), len(data["tools"]))


NODE_CLASS_MAPPINGS = {"DeciiaWorkstation": DeciiaWorkstation}
NODE_DISPLAY_NAME_MAPPINGS = {"DeciiaWorkstation": "Deciia 工作台 (Workstation)"}

from rich.cells import cell_len

from kairospy.surface.workbench.widgets import ActionItem
from kairospy.surface.workbench.widgets.guided_action_list import (
    _action_heading_cell_width,
    _guided_action_prompt,
)


def test_guided_action_descriptions_align_by_terminal_cell_width() -> None:
    items = (
        ActionItem("validate", "校验配置", "检查配置结构和所需资源", "1"),
        ActionItem("status", "查看运行状态", "读取策略与依赖组件状态", "2"),
        ActionItem("edit", "编辑配置", "逐字段修改 Launch 配置", "e"),
    )
    heading_width = max(_action_heading_cell_width(item) for item in items)

    prompts = (
        _guided_action_prompt(item, heading_width=heading_width) for item in items
    )
    separator_columns = {
        cell_len(str(prompt).partition("·")[0]) for prompt in prompts
    }

    assert separator_columns == {heading_width + 2}

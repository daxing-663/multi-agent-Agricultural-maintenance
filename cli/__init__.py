"""CLI 包。

注意：这里**不要**再导入 ``cli.main``。入口脚本 ``python -m cli.main``
会先导入包、再执行模块，包内提前导入会在 sys.modules 里留下重复模块并告警。
命令行的注册点是 pyproject 的 ``agriagents = "cli.main:app"``。
"""

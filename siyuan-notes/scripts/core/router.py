#!/usr/bin/env python3
"""
命令路由器
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from core.exceptions import SiyuanError


class CommandRouter:
    """命令路由器"""

    def __init__(self):
        """初始化路由器"""
        self.modules = {}
        self._register_modules()

    def _register_modules(self):
        """注册所有模块"""
        # 延迟导入，避免循环依赖
        from modules.notebook.command import NotebookCommand
        from modules.document.command import DocumentCommand
        from modules.block.command import BlockCommand
        from modules.query.command import QueryCommand
        from modules.asset.command import AssetCommand
        from modules.export.command import ExportCommand

        self.modules = {
            'nb': NotebookCommand(),
            'notebook': NotebookCommand(),
            'doc': DocumentCommand(),
            'document': DocumentCommand(),
            'blk': BlockCommand(),
            'block': BlockCommand(),
            'query': QueryCommand(),
            'asset': AssetCommand(),
            'export': ExportCommand(),
        }

    def route(self, module_name: str, action: str, **kwargs):
        """
        路由命令到对应模块
        
        Args:
            module_name: 模块名称
            action: 动作名称
            **kwargs: 其他参数
            
        Returns:
            命令执行结果
        """
        module = self.modules.get(module_name)
        
        if not module:
            available = ', '.join(self.modules.keys())
            raise SiyuanError(
                f"未知模块: {module_name}\n"
                f"可用模块: {available}"
            )
        
        # 获取命令方法
        cmd_method = getattr(module, action, None)
        
        if not cmd_method:
            raise SiyuanError(
                f"模块 '{module_name}' 不支持命令: {action}"
            )
        
        # 执行命令
        return cmd_method(**kwargs)

    def list_modules(self):
        """列出所有可用模块"""
        return list(self.modules.keys())

    def get_module(self, module_name: str):
        """获取模块实例"""
        return self.modules.get(module_name)

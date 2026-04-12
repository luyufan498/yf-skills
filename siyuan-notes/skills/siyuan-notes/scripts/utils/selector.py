#!/usr/bin/env python3
"""
交互式选择器
"""

from typing import List, Any, Dict


class InteractiveSelector:
    """交互式选择器"""

    @staticmethod
    def select_notebook(notebooks: List[Dict]) -> Dict:
        """
        让用户选择笔记本
        
        Args:
            notebooks: 笔记本列表
            
        Returns:
            选中的笔记本
        """
        if not notebooks:
            raise ValueError("没有可用的笔记本")
        
        print("\n请选择笔记本:")
        for i, nb in enumerate(notebooks, 1):
            closed = " (已关闭)" if nb.get('closed', False) else ""
            print(f"  {i}. {nb['name']}{closed}")
        
        while True:
            try:
                choice = input("\n输入序号 (默认 1): ").strip() or "1"
                index = int(choice) - 1
                if 0 <= index < len(notebooks):
                    return notebooks[index]
                print(f"✗ 请输入 1-{len(notebooks)} 之间的数字")
            except (ValueError, KeyboardInterrupt):
                print("\n操作已取消")
                raise

    @staticmethod
    def select_document(documents: List[Dict]) -> Dict:
        """
        让用户选择文档
        
        Args:
            documents: 文档列表
            
        Returns:
            选中的文档
        """
        if not documents:
            raise ValueError("没有可用的文档")
        
        print("\n请选择文档:")
        for i, doc in enumerate(documents, 1):
            title = doc.get('title') or doc.get('content', 'Untitled')
            print(f"  {i}. {title}")
        
        while True:
            try:
                choice = input("\n输入序号 (默认 1): ").strip() or "1"
                index = int(choice) - 1
                if 0 <= index < len(documents):
                    return documents[index]
                print(f"✗ 请输入 1-{len(documents)} 之间的数字")
            except (ValueError, KeyboardInterrupt):
                print("\n操作已取消")
                raise

    @staticmethod
    def confirm(message: str, default: bool = False) -> bool:
        """
        确认操作
        
        Args:
            message: 确认消息
            default: 默认值
            
        Returns:
            用户是否确认
        """
        prompt = f"{message} ({'Y/n' if default else 'y/N'}): "
        try:
            response = input(prompt).strip().lower()
            if not response:
                return default
            return response in ['y', 'yes']
        except KeyboardInterrupt:
            print("\n操作已取消")
            return False

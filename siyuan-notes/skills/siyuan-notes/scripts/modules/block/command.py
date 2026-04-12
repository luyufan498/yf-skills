#!/usr/bin/env python3
"""
块命令处理器
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))

from modules.block.client import BlockClient
from utils.format import OutputFormatter
from core.exceptions import SiyuanError


class BlockCommand:
    """块命令处理器"""

    def __init__(self):
        self.client = BlockClient()
        self.formatter = OutputFormatter()

    def show(self, block_id: str, format: str = "text"):
        """
        显示块信息
        
        Args:
            block_id: 块 ID
            format: 输出格式
        """
        try:
            block = self.client.get_block(block_id)
            
            if format == "json":
                import json
                data = {
                    "id": block.id,
                    "type": block.type,
                    "content": block.content,
                    "attrs": block.attrs
                }
                print(json.dumps(data, ensure_ascii=False, indent=2))
            else:
                print(f"\n📦 块信息")
                print("=" * 60)
                print(f"ID:      {block.id}")
                print(f"类型:    {block.type}")
                print(f"内容:    {block.content[:100]}...")
                print(f"属性:    {block.attrs}")
                
        except SiyuanError as e:
            print(f"✗ 错误: {e}")

    def info(self, block_id: str):
        """
        显示块元信息
        
        Args:
            block_id: 块 ID
        """
        try:
            block = self.client.get_block(block_id)
            attrs = self.client.get_block_attributes(block_id)
            
            print(f"\n📋 块元信息")
            print("=" * 60)
            print(f"ID:       {block.id}")
            print(f"类型:     {block.type}")
            print(f"子类型:   {block.subtype}")
            print(f"父块 ID:  {block.parent_id}")
            print(f"根块 ID:  {block.root_id}")
            print(f"创建时间: {block.created}")
            print(f"更新时间: {block.updated}")
            print(f"\n属性:")
            for k, v in attrs.items():
                print(f"  {k}: {v}")
                
        except SiyuanError as e:
            print(f"✗ 错误: {e}")

    def update(self, block_id: str, content: str):
        """
        更新块内容
        
        Args:
            block_id: 块 ID
            content: 新内容
        """
        try:
            self.client.update_block_content(block_id, content)
            print(f"✓ 已更新块: {block_id}")
            
        except SiyuanError as e:
            print(f"✗ 更新失败: {e}")

    def append(self, parent_id: str, content: str):
        """
        追加子块
        
        Args:
            parent_id: 父块 ID
            content: 内容
        """
        try:
            new_id = self.client.append_child_block(parent_id, content)
            print(f"✓ 已追加子块")
            print(f"  新块 ID: {new_id}")
            
        except SiyuanError as e:
            print(f"✗ 追加失败: {e}")

    def prepend(self, parent_id: str, content: str):
        """
        前置子块
        
        Args:
            parent_id: 父块 ID
            content: 内容
        """
        try:
            new_id = self.client.prepend_child_block(parent_id, content)
            print(f"✓ 已前置子块")
            print(f"  新块 ID: {new_id}")
            
        except SiyuanError as e:
            print(f"✗ 前置失败: {e}")

    def move(self, block_id: str, to: str, after: str = None):
        """
        移动块
        
        Args:
            block_id: 块 ID
            to: 目标父块 ID
            after: 前一个块 ID（可选）
        """
        try:
            self.client.move_block_to(block_id, to, after)
            print(f"✓ 已移动块")
            
        except SiyuanError as e:
            print(f"✗ 移动失败: {e}")

    def delete(self, block_id: str, yes: bool = False):
        """
        删除块

        Args:
            block_id: 块 ID
            yes: 直接确认，无需交互式提示
        """
        try:
            if not yes:
                print(f"⚠️  即将删除块: {block_id}")
                confirm = input("确认删除? (yes/no): ").strip().lower()

                if confirm not in ['yes', 'y']:
                    print("操作已取消")
                    return

            self.client.delete_block_by_id(block_id)
            print(f"✓ 已删除块: {block_id}")

        except SiyuanError as e:
            print(f"✗ 删除失败: {e}")
        except EOFError:
            print(f"✗ 操作取消：无法获取用户确认（使用 -y 跳过确认）")

    def attr(self, block_id: str, action: str, key: str = None, 
            value: str = None):
        """
        块属性操作
        
        Args:
            block_id: 块 ID
            action: 操作类型
            key: 属性键
            value: 属性值
        """
        try:
            if action == "get":
                attrs = self.client.get_block_attributes(block_id)
                if key:
                    print(f"{key}: {attrs.get(key, '未设置')}")
                else:
                    print(self.formatter.json(attrs))
                    
            elif action == "set":
                if not key or not value:
                    print("✗ 请提供属性键和值")
                    return
                attrs = self.client.get_block_attributes(block_id)
                attrs[key] = value
                self.client.set_block_attribute(block_id, attrs)
                print(f"✓ 已设置属性: {key} = {value}")
                
            elif action == "unset":
                if not key:
                    print("✗ 请提供属性键")
                    return
                attrs = self.client.get_block_attributes(block_id)
                if key in attrs:
                    del attrs[key]
                    self.client.set_block_attribute(block_id, attrs)
                    print(f"✓ 已删除属性: {key}")
                else:
                    print(f"属性不存在: {key}")
                    
        except SiyuanError as e:
            print(f"✗ 操作失败: {e}")

#!/usr/bin/env python3
"""
思源笔记数据模型
"""

from dataclasses import dataclass, field
from typing import Dict


@dataclass
class Notebook:
    """笔记本数据模型"""
    id: str
    name: str
    icon: str = ""
    sort: int = 0
    closed: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> 'Notebook':
        """从字典创建 Notebook 对象"""
        return cls(
            id=data.get('id', ''),
            name=data.get('name', ''),
            icon=data.get('icon', ''),
            sort=data.get('sort', 0),
            closed=data.get('closed', False)
        )


@dataclass
class Document:
    """文档数据模型"""
    id: str
    notebook_id: str
    title: str
    hpath: str
    path: str
    created: str
    updated: str
    content: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> 'Document':
        """从字典创建 Document 对象"""
        return cls(
            id=data.get('id', ''),
            notebook_id=data.get('box', ''),
            title=data.get('content', ''),
            hpath=data.get('hpath', ''),
            path=data.get('path', ''),
            created=data.get('created', ''),
            updated=data.get('updated', ''),
            content=data.get('content', '')
        )


@dataclass
class Block:
    """块数据模型"""
    id: str
    type: str
    content: str
    parent_id: str
    root_id: str
    attrs: Dict[str, str] = field(default_factory=dict)
    subtype: str = ""
    created: str = ""
    updated: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> 'Block':
        """从字典创建 Block 对象"""
        return cls(
            id=data.get('id', ''),
            type=data.get('type', ''),
            content=data.get('content', ''),
            parent_id=data.get('parent_id', ''),
            root_id=data.get('root_id', ''),
            attrs=data.get('attrs', {}),
            subtype=data.get('subtype', ''),
            created=data.get('created', ''),
            updated=data.get('updated', '')
        )

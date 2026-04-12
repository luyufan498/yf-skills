#!/usr/bin/env python3
"""
树状结构渲染器
"""

from typing import Dict, List


class TreeRenderer:
    """树状结构渲染器"""

    def __init__(self, indent: str = "│   ", branch: str = "├─ ", last: str = "└─ "):
        """
        初始化渲染器
        
        Args:
            indent: 缩进字符
            branch: 分支字符
            last: 最后一个分支字符
        """
        self.indent = indent
        self.branch = branch
        self.last = last

    def render_notebooks(self, notebooks: List[Dict], with_docs: bool = False):
        """
        渲染笔记本列表
        
        Args:
            notebooks: 笔记本列表
            with_docs: 是否显示文档树
        """
        for i, nb in enumerate(notebooks):
            is_last = (i == len(notebooks) - 1)
            prefix = self.last if is_last else self.branch
            
            status = "📖" if not nb.get('closed', False) else "📕"
            print(f"{prefix}{status} {nb['name']}")
            
            if with_docs and nb.get('documents'):
                self._render_documents(nb['documents'], prefix="", is_root=False)

    def render_documents(self, documents: List[Dict], max_depth: int = None):
        """
        渲染文档树

        Args:
            documents: 文档列表
            max_depth: 最大深度，None 表示无限制
        """
        tree = self._build_tree(documents)
        # 对于文档树，不应该是 root=True，这样会有缩进
        self._render_tree(tree, root=False, depth=0, max_depth=max_depth)

    def _build_tree(self, documents: List[Dict]) -> Dict:
        """构建树状结构"""
        tree = {}
        for doc in documents:
            hpath = doc.get('hpath', '').strip('/')
            if not hpath:
                continue

            parts = hpath.split('/')
            if len(parts) == 0:
                continue

            current = tree
            # 遍历路径的每个部分
            for i, part in enumerate(parts):
                if part not in current:
                    current[part] = {'__children__': {}, '__info__': None}

                # 如果这是最后一个部分，标记为实际文档
                if i == len(parts) - 1:
                    current[part]['__info__'] = doc

                # 移动到下一级（即使这是最后一个，也要移动以便子文档可以挂载）
                current = current[part]['__children__']

        return tree

    def _render_tree(self, tree: Dict, prefix: str = "", root: bool = True,
                     depth: int = 0, max_depth: int = None):
        """
        递归渲染树

        Args:
            tree: 树结构字典
            prefix: 前缀字符串
            root: 是否是根节点
            depth: 当前深度
            max_depth: 最大深度
        """
        if max_depth is not None and depth > max_depth:
            return

        # 只处理非 __children__ 和非 __info__ 的键（即路径节点）
        items = [(k, v) for k, v in tree.items() if k not in ('__children__', '__info__')]

        for i, (name, node) in enumerate(items):
            is_last = (i == len(items) - 1)

            if root:
                connector = ""
                next_prefix = ""
            else:
                connector = self.last if is_last else self.branch
                next_prefix = prefix + (self.indent if not is_last else "    ")

            # 判断节点类型
            children = node.get('__children__', {})
            has_info = node.get('__info__') is not None
            has_children = bool([k for k in children.keys() if k not in ('__children__', '__info__')])

            if has_info:
                # 这是一个实际文档节点（可能是父文档或子文档）
                if has_children:
                    # 有子文档的父文档：使用打开的文件夹图标
                    print(f"{prefix}{connector}📂 {name}")
                else:
                    # 叶子文档：普通文档图标
                    print(f"{prefix}{connector}📄 {name}")
            else:
                # 这是虚拟的中间节点（路径的一部分）：使用文件夹图标
                print(f"{prefix}{connector}📁 {name}")

            # 递归渲染子节点
            self._render_tree(children, next_prefix, False, depth + 1, max_depth)

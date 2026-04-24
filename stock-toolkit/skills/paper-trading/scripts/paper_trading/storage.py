"""数据存储模块

提供JSON文件存储和可选的数据库存储
"""

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, List
from datetime import datetime

from paper_trading.models import Account, AccountHistory, Operation


class StorageBackend(ABC):
    """存储后端抽象接口"""

    @abstractmethod
    def save_account(self, account: Account) -> Path:
        """保存账户信息"""
        pass

    @abstractmethod
    def load_account(self, stock_name: str) -> Optional[Account]:
        """加载账户信息"""
        pass

    @abstractmethod
    def delete_account(self, stock_name: str) -> bool:
        """删除账户信息"""
        pass

    @abstractmethod
    def save_operation(self, stock_name: str, operation: Operation) -> Path:
        """保存操作记录"""
        pass

    @abstractmethod
    def load_operations(self, stock_name: str) -> Optional[AccountHistory]:
        """加载操作记录"""
        pass

    @abstractmethod
    def save_operations(self, stock_name: str, operations: AccountHistory) -> Path:
        """覆盖保存完整的操作记录"""
        pass

    @abstractmethod
    def list_accounts(self) -> List[str]:
        """列出所有账户"""
        pass


class JsonStorage(StorageBackend):
    """JSON文件存储实现"""

    def __init__(self, base_dir: str = None):
        """
        初始化JSON存储

        Args:
            base_dir: 基础目录路径，默认使用配置文件中的 tradings_dir
        """
        if base_dir:
            self.base_dir = Path(base_dir)
        else:
            from paper_trading.config import get_workspace_config
            config = get_workspace_config()
            self.base_dir = config['tradings_dir']

        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _get_account_dir(self, stock_name: str) -> Path:
        """
        获取账户目录

        直接使用原始股票名称，不再进行字符清理
        目录结构: tradings_dir/stock_name
        """
        return self.base_dir / stock_name

    def _get_account_file(self, stock_name: str) -> Path:
        """获取账户文件路径

        优先使用新格式 account.json，如果不存在则尝试旧格式 holdings.json
        """
        account_dir = self._get_account_dir(stock_name)
        new_file = account_dir / "account.json"
        old_file = account_dir / "holdings.json"

        # 返回存在的文件，优先新格式
        if new_file.exists():
            return new_file
        elif old_file.exists():
            return old_file
        else:
            return new_file  # 返回新格式路径用于创建新文件

    def _get_operations_file(self, stock_name: str) -> Path:
        """获取操作记录文件路径"""
        return self._get_account_dir(stock_name) / "operations.json"

    def save_account(self, account: Account) -> Path:
        """保存账户信息"""
        account_file = self._get_account_file(account.stock_name)
        account_dir = account_file.parent
        account_dir.mkdir(parents=True, exist_ok=True)

        account.updated_at = datetime.now().isoformat()

        with open(account_file, 'w', encoding='utf-8') as f:
            f.write(account.model_dump_json(ensure_ascii=False, indent=2))

        return account_file

    def load_account(self, stock_name: str) -> Optional[Account]:
        """加载账户信息

        如果检测到旧格式数据，会自动迁移为新格式
        """
        account_dir = self._get_account_dir(stock_name)
        new_file = account_dir / "account.json"
        old_file = account_dir / "holdings.json"

        # 决定使用哪个文件
        if new_file.exists():
            account_file = new_file
        elif old_file.exists():
            account_file = old_file
        else:
            return None

        try:
            with open(account_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # 兼容旧格式：为 position 添加缺失的 stock_code 字段
            if 'positions' in data:
                stock_code = data.get('stock_code', '')
                for position in data['positions']:
                    if isinstance(position, dict) and 'stock_code' not in position:
                        position['stock_code'] = stock_code

            account = Account.model_validate(data)

            # 如果读取的是旧格式，自动迁移为新格式
            if account_file == old_file:
                print(f"🔄 检测到旧格式数据，正在迁移到新格式...")
                self.save_account(account)
                print(f"✅ 已迁移到新格式: {new_file}")

                # 备份旧文件
                backup_file = account_dir / "holdings.json.bak"
                old_file.rename(backup_file)
                print(f"📦 备份旧文件: {backup_file}")

            return account
        except (json.JSONDecodeError, Exception) as e:
            print(f"Error loading account: {e}")
            return None

    def delete_account(self, stock_name: str) -> bool:
        """删除账户信息"""
        account_dir = self._get_account_dir(stock_name)

        if not account_dir.exists():
            return False

        import shutil
        try:
            shutil.rmtree(account_dir)
            return True
        except Exception as e:
            print(f"Error deleting account: {e}")
            return False

    def save_operation(self, stock_name: str, operation: Operation) -> Path:
        """保存操作记录"""
        operations = self.load_operations(stock_name)

        if operations is None:
            operations = AccountHistory(
                stock_name=stock_name,
                created_at=datetime.now().isoformat()
            )

        operations.operations.append(operation)
        operations.updated_at = datetime.now().isoformat()

        return self.save_operations(stock_name, operations)

    def save_operations(self, stock_name: str, operations: AccountHistory) -> Path:
        """覆盖保存完整的操作记录"""
        operations.updated_at = datetime.now().isoformat()

        operations_file = self._get_operations_file(stock_name)
        operations_file.parent.mkdir(parents=True, exist_ok=True)

        with open(operations_file, 'w', encoding='utf-8') as f:
            f.write(operations.model_dump_json(ensure_ascii=False, indent=2))

        return operations_file

    def load_operations(self, stock_name: str) -> Optional[AccountHistory]:
        """加载操作记录"""
        operations_file = self._get_operations_file(stock_name)

        if not operations_file.exists():
            return None

        try:
            with open(operations_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # 兼容旧格式中 type 字段可能叫 'operation'
            if isinstance(data, dict) and 'operations' in data:
                # 过滤掉决策记录（向后兼容）
                valid_operations = []
                for op in data['operations']:
                    if isinstance(op, dict):
                        # 兼容不同的字段名
                        if 'type' not in op and 'operation' in op:
                            op['type'] = op['operation']

                        # 跳过决策记录
                        if op.get('type') not in ['decision', 'decision']:
                            valid_operations.append(op)

                # 更新操作列表
                data['operations'] = valid_operations

            return AccountHistory.model_validate(data)
        except (json.JSONDecodeError, Exception) as e:
            print(f"Error loading operations: {e}")
            return None

    def list_accounts(self) -> List[str]:
        """列出所有账户"""
        accounts = []

        if not self.base_dir.exists():
            return accounts

        for stock_dir in self.base_dir.iterdir():
            if not stock_dir.is_dir():
                continue

            # 检查新格式或旧格式
            account_file_new = stock_dir / "account.json"
            account_file_old = stock_dir / "holdings.json"

            account_file = account_file_new if account_file_new.exists() else account_file_old
            if account_file.exists():
                try:
                    with open(account_file, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        accounts.append(data.get('stock_name', stock_dir.name))
                except Exception:
                    pass

        return accounts


class StorageFactory:
    """存储工厂"""

    @staticmethod
    def create_storage(backend: str = "json", **kwargs) -> StorageBackend:
        """
        创建存储后端实例

        Args:
            backend: 后端类型 (json, mongodb)
            **kwargs: 后端特定配置

        Returns:
            StorageBackend 实例
        """
        if backend == "json":
            return JsonStorage(**kwargs)
        elif backend == "mongodb":
            raise NotImplementedError("MongoDB storage not implemented yet")
        else:
            raise ValueError(f"Unknown storage backend: {backend}")

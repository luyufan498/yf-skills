"""数据导出模块

提供数据导出到CSV、JSON等格式的功能
"""

from datetime import datetime
from pathlib import Path
from typing import Optional
import json
import pandas as pd

from paper_trading.storage import StorageBackend


class DataExporter:
    """数据导出器"""

    def __init__(self, storage: Optional[StorageBackend] = None):
        """
        初始化导出器

        Args:
            storage: 存储后端
        """
        from paper_trading.storage import StorageFactory
        self.storage = storage or StorageFactory.create_storage("json")

    def export_operations_to_csv(
        self,
        stock_name: str,
        output_path: Optional[str] = None
    ) -> Optional[str]:
        """
        导出操作记录到CSV

        Args:
            stock_name: 股票名称
            output_path: 输出文件路径，默认自动生成

        Returns:
            输出文件路径，如果失败返回False
        """
        ops_data = self.storage.load_operations(stock_name)

        if not ops_data or not ops_data.operations:
            print(f"❌ 未找到股票 '{stock_name}' 的操作记录")
            return False

        rows = []
        for op in ops_data.operations:
            row = {
                "时间": op.timestamp,
                "类型": op.type.value if hasattr(op.type, 'value') else str(op.type),
                "价格": op.price or 0,
                "数量": op.quantity or 0,
                "金额": op.amount or op.capital or 0,
                "成本": op.cost or 0,
                "盈亏": op.profit or 0,
                "备注": op.note or "",
            }
            rows.append(row)

        df = pd.DataFrame(rows)

        if output_path is None:
            output_path = f"/tmp/{stock_name}_operations_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

        df.to_csv(output_path, index=False, encoding='utf-8-sig')
        print(f"✅ 已导出到: {output_path}")

        return output_path

    def export_holdings_to_json(
        self,
        stock_name: str,
        output_path: Optional[str] = None
    ) -> Optional[str]:
        """
        导出持仓数据到JSON

        Args:
            stock_name: 股票名称
            output_path: 输出文件路径，默认自动生成

        Returns:
            输出文件路径，如果失败返回False
        """
        from paper_trading.portfolio import PortfolioManager

        manager = PortfolioManager(storage=self.storage)
        account = manager.trader.get_account(stock_name)

        if not account:
            print(f"❌ 未找到股票 '{stock_name}' 的持仓记录")
            return False

        export_data = {
            "stock_name": account.stock_name,
            "stock_code": account.stock_code,
            "created_at": account.created_at,
            "updated_at": account.updated_at,
            "capital_pool": account.capital_pool.model_dump(),
            "positions": [pos.model_dump() for pos in account.positions],
        }

        if output_path is None:
            output_path = f"/tmp/{stock_name}_holdings_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(json.dumps(export_data, ensure_ascii=False, indent=2))

        print(f"✅ 已导出到: {output_path}")

        return output_path

    def export_all_to_json(
        self,
        output_path: Optional[str] = None
    ) -> Optional[str]:
        """
        导出所有数据到JSON

        Args:
            output_path: 输出文件路径，默认自动生成

        Returns:
            输出文件路径，如果失败返回False
        """
        from paper_trading.portfolio import PortfolioManager

        manager = PortfolioManager(storage=self.storage)
        account_names = manager.list_accounts()

        if not account_names:
            print("❌ 没有可导出的数据")
            return False

        export_data = {
            "export_time": datetime.now().isoformat(),
            "accounts": []
        }

        for name in account_names:
            account = manager.trader.get_account(name)
            operations = self.storage.load_operations(name)

            if account:
                account_data = {
                    "stock_name": account.stock_name,
                    "stock_code": account.stock_code,
                    "capital_pool": account.capital_pool.model_dump(),
                    "positions": [pos.model_dump() for pos in account.positions],
                }

                if operations:
                    account_data["operations"] = [op.model_dump() for op in operations.operations]

                export_data["accounts"].append(account_data)

        if output_path is None:
            output_path = f"/tmp/portfolio_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(json.dumps(export_data, ensure_ascii=False, indent=2))

        print(f"✅ 已导出 {len(account_names)} 个账户到: {output_path}")

        return output_path

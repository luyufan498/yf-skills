#!/usr/bin/env python3
"""
资源文件命令处理器
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))

from core.client import SiyuanClient
from core.exceptions import SiyuanError
from pathlib import Path
from modules.asset.client import AssetClient


class AssetCommand:
    """资源文件命令处理器"""

    def __init__(self):
        self.client = AssetClient()

    def upload(self, file: str, to: str = "/assets/"):
        """
        上传资源文件

        Args:
            file: 文件路径
            to: 目标路径
        """
        try:
            file_path = Path(file)
            if not file_path.exists():
                print(f"✗ 文件不存在: {file}")
                return

            # 调用 AssetClient.upload_file() 上传
            data = self.client.upload_file(str(file_path), to)

            if data.get("succMap"):
                print(f"✓ 上传成功:")
                for path, url in data["succMap"].items():
                    print(f"  {path} → {url}")
            else:
                print("✗ 上传失败")

        except SiyuanError as e:
            print(f"✗ 上传失败: {e}")
        except Exception as e:
            print(f"✗ 错误: {e}")

    def _normalize_path(self, path: str) -> str:
        """
        标准化路径为 API 格式

        Args:
            path: 用户输入的路径

        Returns:
            API 调用用的完整路径
        """
        path = path.strip()

        # 绝对路径（以 /data/ 开头）
        if path.startswith("/data/"):
            return path

        # assets 相对路径
        if path.startswith("/assets/"):
            return "/data" + path

        # 纯文件名
        if not path.startswith("/"):
            return f"/data/assets/{path}"

        # 其他情况（如 /foo/bar.png）
        return f"/data/assets/{path.lstrip('/')}"

    def download(self, path: str, output: str = None):
        """
        下载资源文件

        Args:
            path: 文件路径（支持相对路径、assets路径或绝对路径）
            output: 输出路径（可选，默认: ./assets/文件名）
        """
        # 检测是否在 skill 项目内部执行（仅在没有指定 output 时检查）
        if not output:
            current_dir = Path.cwd()
            # 获取 skill 项目根目录（从 scripts/modules/asset/command.py 向上4级）
            skill_root = Path(__file__).parent.parent.parent.parent.absolute()

            # 检查当前目录是否在 skill 项目内部
            if current_dir == skill_root or current_dir.is_relative_to(skill_root):
                print("✗ 不允许在 skill 安装目录中下载文件")
                print(f"  当前目录: {current_dir}")
                print(f"  请在其他目录执行，或使用 --output 参数指定输出路径")
                return

        try:
            # 标准化路径
            api_path = self._normalize_path(path)
            print(f"正在下载: {Path(api_path).name}")

            # 调用 API 获取文件
            file_content, filename = self.client.get_file(api_path)

            # 确定保存路径
            if output:
                save_path = Path(output)
                if save_path.is_dir():
                    save_path = save_path / filename
            else:
                save_path = Path("./assets") / filename

            # 创建目录
            save_path.parent.mkdir(parents=True, exist_ok=True)

            # 显示文件大小
            size = len(file_content)
            if size < 1024:
                size_str = f"{size} B"
            elif size < 1024 * 1024:
                size_str = f"{size / 1024:.1f} KB"
            else:
                size_str = f"{size / (1024 * 1024):.1f} MB"
            print(f"文件大小: {size_str}")

            # 写入文件
            with open(save_path, 'wb') as f:
                f.write(file_content)

            print(f"保存到: {save_path.absolute()}")
            print("✓ 下载完成")

        except SiyuanError as e:
            print(f"✗ 下载失败: {e}")
        except Exception as e:
            print(f"✗ 错误: {e}")

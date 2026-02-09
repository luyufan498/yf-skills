#!/usr/bin/env python3
"""
资源文件客户端
"""

from core.client import SiyuanClient
from pathlib import Path
from core.exceptions import SiyuanError
import requests


class AssetClient(SiyuanClient):
    """资源文件客户端"""

    def get_file(self, path: str) -> tuple[bytes, str]:
        """
        获取文件内容

        Args:
            path: 文件路径（相对于工作空间，如 /data/assets/image.png）

        Returns:
            (文件内容bytes, 文件名)

        Raises:
            SiyuanError: API 返回错误
        """
        url = f"{self.endpoint}/api/file/getFile"
        headers = {"Authorization": f"Token {self.token}"}

        try:
            response = self.session.post(url, headers=headers, json={"path": path})

            if response.status_code == 200:
                # 成功，返回二进制内容
                return response.content, Path(path).name

            elif response.status_code == 202:
                # 错误响应，解析 JSON
                result = response.json()
                error_map = {
                    -1: "参数解析错误",
                    403: "无访问权限（文件不在工作空间下）",
                    404: "文件不存在",
                    405: "这是一个目录，不是文件",
                    500: "服务器错误（文件读取失败）"
                }
                code = result.get("code")
                msg = error_map.get(code, result.get("msg", "未知错误"))
                raise SiyuanError(msg)
            else:
                raise SiyuanError(f"HTTP {response.status_code}")

        except requests.RequestException as e:
            raise SiyuanError(f"网络请求失败: {e}")

    def upload_file(self, file_path: str, to: str = "/assets/") -> dict:
        """
        上传文件

        Args:
            file_path: 本地文件路径
            to: 目标路径

        Returns:
            API 响应结果

        Raises:
            SiyuanError: API 返回错误
        """
        url = f"{self.endpoint}/api/asset/upload"

        try:
            file_path_obj = Path(file_path)
            files = {
                "file[]": (
                    file_path_obj.name,
                    open(file_path, "rb"),
                    "application/octet-stream"
                )
            }

            data = {"assetsDirPath": to}

            # 临时移除 Content-Type header，让 requests 自动设置 multipart/form-data
            headers = {"Authorization": f"Token {self.token}"}
            original_headers = self.session.headers.copy()
            if 'Content-Type' in self.session.headers:
                del self.session.headers['Content-Type']

            try:
                # 不传递 headers 参数，让 requests 从 session.headers 中读取
                response = self.session.post(url, files=files, data=data)
            finally:
                # 恢复原始 headers
                self.session.headers = original_headers

            response.raise_for_status()
            result = response.json()

            if result.get("code") != 0:
                raise SiyuanError(result.get("msg", "Upload failed"))

            return result.get("data", {})

        except FileNotFoundError:
            raise SiyuanError(f"文件不存在: {file_path}")
        except Exception as e:
            raise SiyuanError(f"上传失败: {e}")

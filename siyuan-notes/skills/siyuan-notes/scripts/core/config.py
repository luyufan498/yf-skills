#!/usr/bin/env python3
"""
思源笔记配置管理
"""

import os
import json
from typing import Dict, Any, Optional


class Config:
    """配置管理"""

    DEFAULT_ENDPOINT = "http://127.0.0.1:6806"
    CONFIG_FILE = os.path.expanduser("~/.siyuan/config.json")

    @classmethod
    def _ensure_config_dir(cls):
        """确保配置目录存在"""
        config_dir = os.path.dirname(cls.CONFIG_FILE)
        if config_dir and not os.path.exists(config_dir):
            os.makedirs(config_dir, exist_ok=True)

    @classmethod
    def load_config(cls) -> Dict[str, Any]:
        """加载配置文件"""
        if os.path.exists(cls.CONFIG_FILE):
            try:
                with open(cls.CONFIG_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return {}
        return {}

    @classmethod
    def save_config(cls, config: Dict[str, Any]) -> None:
        """保存配置到文件"""
        cls._ensure_config_dir()
        with open(cls.CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)

    @classmethod
    def get_endpoint(cls) -> str:
        """获取 API 端点"""
        endpoint = os.getenv('SIYUAN_ENDPOINT')
        if endpoint:
            return endpoint
        config = cls.load_config()
        return config.get('endpoint', cls.DEFAULT_ENDPOINT)

    @classmethod
    def get_token(cls) -> Optional[str]:
        """获取 API Token"""
        token = os.getenv('SIYUAN_TOKEN')
        if token:
            return token
        config = cls.load_config()
        return config.get('token')

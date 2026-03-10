#!/usr/bin/env python3
"""
广发证券 MCP API 客户端
提供统一的 MCP 服务访问接口
"""

import requests
import json
import sys
import os

# 使用动态路径：基于脚本位置查找配置文件
# 脚本位置: gf-finance/scripts/mcp_client.py
# 配置位置: gf-finance/assets/mcp_config.json
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
CONFIG_FILE = os.path.join(PROJECT_DIR, 'assets', 'mcp_config.json')

class GFMCPClient:
    """广发证券 MCP 服务客户端"""

    def __init__(self, config_path=CONFIG_FILE):
        """初始化客户端，加载配置"""
        self.load_config(config_path)

    def load_config(self, config_path):
        """加载配置文件"""
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
                self.mcpServers = config.get('mcpServers', {})
                # 默认使用配置中的第一个服务器
                self.token = None
                self.base_url = None
                for name, server in self.mcpServers.items():
                    if 'headers' in server and 'Authorization' in server['headers']:
                        self.token = server['headers']['Authorization'].replace('Bearer ', '')
                        self.base_url = server['url']
                        break
        except Exception as e:
            print(f"错误：加载配置文件失败 - {e}", file=sys.stderr)
            return False
        return True

    def call_tool(self, service, tool_name, params=None):
        """调用 MCP 工具"""
        if params is None:
            params = {}

        try:
            # 构造完整的MCP工具调用
            mcp_request = {
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": params
                },
                "id": 1
            }

            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.token}"
            }

            # 根据服务构造URL
            url = f"https://mcp-api.gf.com.cn/server/mcp/{service}/mcp"

            response = requests.post(url, json=mcp_request, headers=headers, timeout=30)

            if response.status_code == 200:
                result = response.json()

                # 检查响应格式并处理不同的API结构
                if "result" in result:
                    # 标准MCP响应格式
                    if "content" in result["result"] and result["result"]["content"]:
                        content = result["result"]["content"]
                        # 如果content是列表，提取第一个元素的文本内容
                        if isinstance(content, list) and len(content) > 0:
                            if "text" in content[0]:
                                # 解析文本内容中的JSON
                                text_content = content[0]["text"]
                                try:
                                    data = json.loads(text_content)
                                    return json.dumps(data, ensure_ascii=False, indent=2)
                                except json.JSONDecodeError:
                                    return json.dumps({"text": text_content}, ensure_ascii=False, indent=2)
                        return json.dumps({"content": content}, ensure_ascii=False, indent=2)
                    else:
                        return json.dumps(result["result"], ensure_ascii=False, indent=2)
                elif "error" in result:
                    # API返回错误
                    return json.dumps({
                        "error": result["error"].get("message", "API错误"),
                        "code": result["error"].get("code", "UNKNOWN")
                    }, ensure_ascii=False, indent=2)
                else:
                    # 直接返回原始响应
                    return json.dumps(result, ensure_ascii=False, indent=2)
            else:
                return json.dumps({
                    "error": f"HTTP {response.status_code}",
                    "message": response.text
                }, ensure_ascii=False, indent=2)

        except requests.exceptions.RequestException as e:
            return json.dumps({
                "error": "请求失败",
                "message": str(e)
            }, ensure_ascii=False, indent=2)
        except json.JSONDecodeError as e:
            return json.dumps({
                "error": "JSON解析失败",
                "message": str(e)
            }, ensure_ascii=False, indent=2)
        except Exception as e:
            return json.dumps({
                "error": "处理失败",
                "message": str(e)
            }, ensure_ascii=False, indent=2)

def main():
    """命令行接口"""
    if len(sys.argv) < 3:
        print("使用方法: python mcp_client.py <service> <tool_name> [params_json]")
        print("示例: python mcp_client.py lhb lhb_aborttrade_market_date_get '{\"date\":20250528,\"market\":\"sh\"}'")
        sys.exit(1)

    service = sys.argv[1]
    tool_name = sys.argv[2]
    params = {}

    if len(sys.argv) > 3:
        try:
            params = json.loads(sys.argv[3])
        except json.JSONDecodeError as e:
            print(f"错误：参数JSON格式错误 - {e}", file=sys.stderr)
            sys.exit(1)

    client = GFMCPClient()
    result = client.call_tool(service, tool_name, params)
    print(result)

if __name__ == "__main__":
    main()
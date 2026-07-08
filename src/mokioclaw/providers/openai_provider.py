"""OpenAI 兼容模型工厂 —— 使用 langchain_openai 的 ChatOpenAI."""

from __future__ import annotations

import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

load_dotenv()


def create_model(
    model: str | None = None,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    temperature: float = 0.0,
) -> ChatOpenAI:
    """创建 ChatOpenAI 模型实例。

    默认从环境变量 OPENAI_API_KEY 读取密钥，
    也支持 OPENAI_BASE_URL 自定义 endpoint。

    Args:
        model: 模型名称，默认 "gpt-4o"。
        api_key: API 密钥，None 则读取 OPENAI_API_KEY。
        base_url: API endpoint，None 则读取 OPENAI_BASE_URL。
        temperature: 采样温度，默认 0。

    Returns:
        配置好的 ChatOpenAI 实例。
    """
    if api_key is None:
        api_key = os.getenv("API_KEY")
        if not api_key:
            raise ValueError(
                "未设置 API_KEY，请在 .env 文件中配置或设置环境变量"
            )

    if base_url is None:
        base_url = os.getenv("BASE_URL")

    if model is None:
        model = os.getenv("MODEL")

    kwargs: dict = {
        "model": model,
        "api_key": api_key,
        "temperature": temperature,
        "max_retries": 2,
        "request_timeout": 120,
    }
    if base_url:
        kwargs["base_url"] = base_url

    return ChatOpenAI(**kwargs)

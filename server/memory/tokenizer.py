"""
Token 计数器 - 使用 tiktoken
"""
import tiktoken


class Tokenizer:
    """Token 计数器 - 用于预算管理"""

    def __init__(self, model: str = "gpt-4"):
        """
        初始化 tokenizer

        Args:
            model: 模型名称，用于选择对应的 encoding
        """
        self.encoding = tiktoken.encoding_for_model(model)

    def count(self, text: str) -> int:
        """
        计算文本的 token 数

        Args:
            text: 输入文本

        Returns:
            token 数量
        """
        return len(self.encoding.encode(text))

    def encode(self, text: str) -> list[int]:
        """编码文本为 token ids"""
        return self.encoding.encode(text)

    def decode(self, tokens: list[int]) -> str:
        """解码 token ids 为文本"""
        return self.encoding.decode(tokens)

    def truncate(self, text: str, max_tokens: int) -> str:
        """
        截断文本到指定 token 数

        Args:
            text: 输入文本
            max_tokens: 最大 token 数

        Returns:
            截断后的文本
        """
        tokens = self.encoding.encode(text)
        if len(tokens) <= max_tokens:
            return text
        return self.encoding.decode(tokens[:max_tokens])


# 全局实例
tokenizer = Tokenizer()

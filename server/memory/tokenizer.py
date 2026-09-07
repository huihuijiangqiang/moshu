"""
Token 计数器 - 使用 tiktoken
"""
import tiktoken

#: 默认 encoding。实测中文密度差异很大：
#:   cl100k_base  ≈ 1.31 token/汉字  → 25k 预算 ≈ 1.9 万字
#:   o200k_base   ≈ 0.90 token/汉字  → 25k 预算 ≈ 2.8 万字
#: 这里故意选 token 数偏高的 cl100k_base。理由是低估的后果不对称：
#: 低估会让装配结果在真实模型侧超出 25k 上限，请求直接失败或被静默截断
#: （截掉的是 layer4 章末，正好是最关键的情节）；高估只是少送一点前文。
#: 网关接的模型确定后，应换成与之匹配的 encoding，否则预算报表会持续偏保守。
DEFAULT_ENCODING = "cl100k_base"


class Tokenizer:
    """Token 计数器 - 用于预算管理"""

    def __init__(self, encoding_name: str = DEFAULT_ENCODING):
        """
        初始化 tokenizer

        Args:
            encoding_name: tiktoken encoding 名称。直接给 encoding 而不是模型名，
                因为 encoding_for_model 对未知模型会抛 KeyError，
                而网关后面挂的往往不是 OpenAI 模型。
        """
        self.encoding = tiktoken.get_encoding(encoding_name)

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

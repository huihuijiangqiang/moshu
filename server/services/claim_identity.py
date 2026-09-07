"""
claim 身份指纹 - 唯一实现。

指纹既是跨块去重键，也是数据库唯一键（uq_claim_*_source）。它必须回答一个问题：
**两条陈述是不是同一条事实的同一次出现？**

只看语义答不了这个问题：

* 同一句话出现在两条独立时间线上，是两条独立事实。并行 POV 里「李长风还活着」
  在 A 线和 B 线各自成立；合并成一条，其中一条就永远看不见，跨线冲突也永远检
  不出来。
* 同一时间线里不同段落的同语义陈述，是同一事实的两次出现。它们的来源位置不同，
  作者需要分别定位；合并成一条只会保留其中一处的位置，另一处的告警指向错误的
  地方。

所以身份 = 语义 + timeline_id + 稳定的来源锚点（source_anchor，见
services.chunking 的全局段落号）。

**confidence 不参与**：它是模型对同一件事的把握程度，不是身份的一部分。纳入它
会让模型每次微调置信度都插出一条新行，作者已有的处置记录随之失效。
order_basis / order_confidence / story_order 同理不参与 —— 顺序判断是对事实的
时间位置的判断，不是事实本身。

重叠去重仍然成立：相邻块共享的那一段在两块里拿到**同一个**全局段落号，所以同
一处正文重复抽到的同一事实指纹相同。

放在独立模块里，是因为写 claim 的路径有两条（抽取任务走 providers，服务层走
services.consistency.upsert_claim）。两处各算各的哈希时，同一条事实会因为一点
序列化差异（分隔符、ensure_ascii、None 与空串）得到两个指纹，唯一键形同虚设。
"""
from __future__ import annotations

import hashlib
import json
from typing import Optional


def claim_fingerprint(
    *,
    subject_text: str,
    predicate: str,
    object_type: str,
    object_value: Optional[str],
    polarity: str,
    timeline_id: Optional[str] = None,
    source_anchor: Optional[str] = None,
) -> str:
    """稳定的 claim 身份指纹（大小写、空白无关）。

    timeline_id / source_anchor 缺省为空串而不是 None：模型判断不出时间线或来源
    时，所有这类 claim 落在同一个「未知」身份空间里，行为与加入这两个字段之前
    一致。
    """
    fingerprint_data = {
        "subject_text": subject_text.lower().strip(),
        "predicate": predicate.lower().strip(),
        "object_type": object_type,
        "object_value": (object_value or "").lower().strip(),
        "polarity": polarity,
        "timeline_id": (timeline_id or "").strip(),
        "source_anchor": (source_anchor or "").strip(),
    }
    payload = json.dumps(
        fingerprint_data, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = ["claim_fingerprint"]

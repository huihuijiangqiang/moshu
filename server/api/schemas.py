"""
Pydantic 响应模型 - 与前端 TypeScript 类型对齐
"""
from pydantic import BaseModel


class CodexEntryOut(BaseModel):
    """设定库条目"""

    id: str
    kind: str  # character, location, item, faction, event, rule
    name: str
    description: str
    attrs: dict
    resident: bool
    status: str  # confirmed, pending
    ref_chapters: list[str]
    conflicts: list[str]
    planted_at: str | None
    expected_by: str | None

    class Config:
        from_attributes = True


class GuardIssueOut(BaseModel):
    """守卫问题"""

    id: str
    chapter_id: str
    entry_id: str | None
    issue_type: str
    severity: str
    description: str
    evidence: dict
    anchor: dict  # {pid: str, start: int, end: int}
    actions: list[str]
    resolved: bool
    resolution: str | None
    false_positive: bool

    class Config:
        from_attributes = True


class ContextLayerOut(BaseModel):
    """上下文层信息 - 用于右栏展示"""

    key: str  # resident, retrieved, summary, adjacent
    tokens: int
    items: list[dict]


class ContextPreviewOut(BaseModel):
    """上下文预览 - GET /chapters/:id/context"""

    layer1_resident: ContextLayerOut
    layer2_retrieved: ContextLayerOut
    layer3_summary: ContextLayerOut
    layer4_adjacent: ContextLayerOut
    total_tokens: int
    budget_exceeded: bool
    trimmed_layers: list[str]

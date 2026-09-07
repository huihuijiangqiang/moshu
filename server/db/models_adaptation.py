"""漫剧改编层模型。

漫剧是小说项目的一个独立输出版本，不能直接把 Chapter 改造成视频集：
同一部作品未来可以同时拥有小说、漫剧和有声改编。
"""
from typing import Optional

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base, TimestampMixin


class Adaptation(Base, TimestampMixin):
    """一个项目的改编版本，例如「竖屏漫剧第一季」。"""

    __tablename__ = "adaptations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(32), ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    format: Mapped[str] = mapped_column(String(30), default="comic_drama")
    title: Mapped[str] = mapped_column(String(200))
    aspect_ratio: Mapped[str] = mapped_column(String(20), default="9:16")
    style_profile: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="draft")

    episodes: Mapped[list["Episode"]] = relationship(back_populates="adaptation", cascade="all, delete-orphan")
    visual_profiles: Mapped[list["VisualProfile"]] = relationship(
        back_populates="adaptation", cascade="all, delete-orphan"
    )


class Episode(Base, TimestampMixin):
    """漫剧单集，允许映射一个或多个小说章节。"""

    __tablename__ = "adaptation_episodes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    adaptation_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("adaptations.id", ondelete="CASCADE"), index=True
    )
    number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    source_chapter_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    target_duration: Mapped[int] = mapped_column(Integer, default=90)
    status: Mapped[str] = mapped_column(String(20), default="draft")

    adaptation: Mapped["Adaptation"] = relationship(back_populates="episodes")
    scenes: Mapped[list["Scene"]] = relationship(back_populates="episode", cascade="all, delete-orphan")


class Scene(Base, TimestampMixin):
    """单集内的场景段落。"""

    __tablename__ = "adaptation_scenes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    episode_id: Mapped[str] = mapped_column(String(32), ForeignKey("adaptation_episodes.id", ondelete="CASCADE"), index=True)
    order: Mapped[int] = mapped_column(Integer)
    purpose: Mapped[str] = mapped_column(String(200), default="")
    location_entry_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("codex_entries.id", ondelete="SET NULL"), nullable=True
    )
    time_anchor: Mapped[str] = mapped_column(String(100), default="")
    character_entry_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    summary: Mapped[str] = mapped_column(Text, default="")

    episode: Mapped["Episode"] = relationship(back_populates="scenes")
    shots: Mapped[list["Shot"]] = relationship(back_populates="scene", cascade="all, delete-orphan")


class Shot(Base, TimestampMixin):
    """可人工审核的静态分镜，不包含视频渲染状态。"""

    __tablename__ = "adaptation_shots"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    scene_id: Mapped[str] = mapped_column(String(32), ForeignKey("adaptation_scenes.id", ondelete="CASCADE"), index=True)
    order: Mapped[int] = mapped_column(Integer)
    shot_type: Mapped[str] = mapped_column(String(30), default="medium")
    camera: Mapped[str] = mapped_column(String(100), default="static")
    duration_target: Mapped[int] = mapped_column(Integer, default=4)
    action: Mapped[str] = mapped_column(Text, default="")
    dialogue: Mapped[str] = mapped_column(Text, default="")
    narration: Mapped[str] = mapped_column(Text, default="")
    visual_prompt: Mapped[str] = mapped_column(Text, default="")
    reference_asset_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(20), default="draft")

    scene: Mapped["Scene"] = relationship(back_populates="shots")


class VisualProfile(Base, TimestampMixin):
    """Codex 条目的视觉档案，确保同一人物跨镜头使用同一套约束。"""

    __tablename__ = "adaptation_visual_profiles"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    adaptation_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("adaptations.id", ondelete="CASCADE"), index=True
    )
    codex_entry_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("codex_entries.id", ondelete="CASCADE"), index=True
    )
    display_name: Mapped[str] = mapped_column(String(200))
    style: Mapped[str] = mapped_column(String(100), default="")
    appearance: Mapped[str] = mapped_column(Text, default="")
    costume: Mapped[str] = mapped_column(Text, default="")
    palette: Mapped[list[str]] = mapped_column(JSONB, default=list)
    reference_asset_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    version: Mapped[int] = mapped_column(Integer, default=1)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str] = mapped_column(Text, default="")

    adaptation: Mapped["Adaptation"] = relationship(back_populates="visual_profiles")

import pytest

from services.temporal_anchor import TemporalAnchorError, format_temporal_anchor, normalize_temporal_anchor


def test_temporal_anchor_normalizes_and_formats_range():
    anchor = normalize_temporal_anchor({"start": "2024-03-12", "end": "2024-03-20", "precision": "day"})
    assert anchor == {"start": "2024-03-12", "end": "2024-03-20", "precision": "day"}
    assert "2024-03-12" in format_temporal_anchor(anchor)
    assert "2024-03-20" in format_temporal_anchor(anchor)


@pytest.mark.parametrize(
    "value",
    [
        {"start": "2024-03-20", "end": "2024-03-12"},
        {"start": "三月十二日"},
        {"start": "2024-03-12", "precision": "hour"},
    ],
)
def test_temporal_anchor_rejects_ambiguous_or_backwards_values(value):
    with pytest.raises(TemporalAnchorError):
        normalize_temporal_anchor(value)


def test_temporal_anchor_accepts_single_day_without_end():
    assert normalize_temporal_anchor({"start": "2024-03-12"})["end"] == "2024-03-12"

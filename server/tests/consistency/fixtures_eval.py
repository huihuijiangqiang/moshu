"""
P0 consistency evaluation fixtures - positive examples and hard negatives
"""

# P0 规则覆盖：
# 1. 生死冲突
# 2. 物品归属冲突
# 3. 知情边界违规

POSITIVE_CASES = [
    {
        "id": "alive_001",
        "rule": "alive_conflict",
        "description": "角色已死亡但后续章节仍活跃",
        "project_id": "proj_test",
        "timeline_id": "main",
        "claims": [
            {
                "subject": "沈砚",
                "predicate": "alive",
                "object_value": "false",
                "story_order": 100.0,
                "chapter_id": "ch_05",
                "body_rev": 3,
                "paragraph_id": "p_5_12",
            },
            {
                "subject": "沈砚",
                "predicate": "alive",
                "object_value": "true",
                "story_order": 150.0,
                "chapter_id": "ch_08",
                "body_rev": 2,
                "paragraph_id": "p_8_03",
            },
        ],
        "expected_conflict": True,
        "expected_evidence": ["ch_05:3:p_5_12", "ch_08:2:p_8_03"],
    },
    {
        "id": "ownership_001",
        "rule": "ownership_conflict",
        "description": "同一物品同时归属两人",
        "project_id": "proj_test",
        "timeline_id": "main",
        "claims": [
            {
                "subject": "玉佩",
                "predicate": "owned_by",
                "object_entity": "沈砚",
                "story_order": 80.0,
                "chapter_id": "ch_04",
                "body_rev": 1,
                "paragraph_id": "p_4_08",
            },
            {
                "subject": "玉佩",
                "predicate": "owned_by",
                "object_entity": "苏清",
                "story_order": 80.0,
                "chapter_id": "ch_04",
                "body_rev": 1,
                "paragraph_id": "p_4_15",
            },
        ],
        "expected_conflict": True,
        "expected_evidence": ["ch_04:1:p_4_08", "ch_04:1:p_4_15"],
    },
    {
        "id": "knowledge_001",
        "rule": "knowledge_boundary",
        "description": "角色使用尚未获知的信息",
        "project_id": "proj_test",
        "timeline_id": "main",
        "claims": [
            {
                "subject": "苏清",
                "predicate": "knows_fact",
                "object_value": "沈砚真实身份",
                "story_order": 120.0,
                "chapter_id": "ch_07",
                "body_rev": 2,
                "paragraph_id": "p_7_20",
            },
            {
                "subject": "苏清",
                "predicate": "acts_on_knowledge",
                "object_value": "沈砚真实身份",
                "story_order": 80.0,
                "chapter_id": "ch_05",
                "body_rev": 1,
                "paragraph_id": "p_5_08",
            },
        ],
        "expected_conflict": True,
        "expected_evidence": ["ch_05:1:p_5_08", "ch_07:2:p_7_20"],
    },
]

HARD_NEGATIVES = [
    {
        "id": "hn_conditional_ability",
        "description": "条件性能力陈述 - 不冲突",
        "project_id": "proj_test",
        "timeline_id": "main",
        "claims": [
            {
                "subject": "沈砚",
                "predicate": "sword_skill",
                "object_value": "poor",
                "certainty": "explicit",
                "story_order": 50.0,
                "chapter_id": "ch_02",
                "body_rev": 1,
                "paragraph_id": "p_2_05",
                "context": "沈砚不擅长用剑",
            },
            {
                "subject": "沈砚",
                "predicate": "sword_skill",
                "object_value": "barely_usable",
                "certainty": "explicit",
                "story_order": 60.0,
                "chapter_id": "ch_03",
                "body_rev": 1,
                "paragraph_id": "p_3_12",
                "context": "危急时勉强拔剑",
                "condition": "emergency",
            },
        ],
        "expected_conflict": False,
        "reason": "条件限定明确，不冲突",
    },
    {
        "id": "hn_temporal_change",
        "description": "时间演变的状态变化 - 不冲突",
        "project_id": "proj_test",
        "timeline_id": "main",
        "claims": [
            {
                "subject": "北狄山道",
                "predicate": "snow_state",
                "object_value": "melting",
                "story_order": 40.0,
                "chapter_id": "ch_02",
                "body_rev": 1,
                "paragraph_id": "p_2_01",
                "context": "四月初，积雪开始融化",
            },
            {
                "subject": "北狄山道",
                "predicate": "snow_state",
                "object_value": "patches_remain",
                "story_order": 45.0,
                "chapter_id": "ch_02",
                "body_rev": 1,
                "paragraph_id": "p_2_18",
                "context": "四月底山阴仍有残雪",
            },
        ],
        "expected_conflict": False,
        "reason": "时间不同，状态演变合理",
    },
    {
        "id": "hn_treatment_effect",
        "description": "治疗后症状改善 - 不冲突",
        "project_id": "proj_test",
        "timeline_id": "main",
        "claims": [
            {
                "subject": "沈砚",
                "predicate": "shoulder_condition",
                "object_value": "pain_severe",
                "story_order": 30.0,
                "chapter_id": "ch_01",
                "body_rev": 2,
                "paragraph_id": "p_1_15",
                "context": "左肩旧伤遇寒痛",
                "trigger": "cold",
            },
            {
                "subject": "沈砚",
                "predicate": "shoulder_condition",
                "object_value": "pain_reduced",
                "story_order": 32.0,
                "chapter_id": "ch_01",
                "body_rev": 2,
                "paragraph_id": "p_1_20",
                "context": "服药后症状减轻",
                "cause": "medication",
            },
        ],
        "expected_conflict": False,
        "reason": "因果关系明确，不冲突",
    },
    {
        "id": "hn_age_progression",
        "description": "年龄渐变 - 不冲突",
        "project_id": "proj_test",
        "timeline_id": "main",
        "claims": [
            {
                "subject": "苏清",
                "predicate": "appearance",
                "object_value": "youthful_handsome",
                "story_order": 10.0,
                "chapter_id": "ch_01",
                "body_rev": 1,
                "paragraph_id": "p_1_03",
                "context": "年轻时的苏清英俊潇洒",
                "age_range": "young",
            },
            {
                "subject": "苏清",
                "predicate": "appearance",
                "object_value": "mature_dignified",
                "story_order": 200.0,
                "chapter_id": "ch_15",
                "body_rev": 1,
                "paragraph_id": "p_15_05",
                "context": "中年的苏清威严沉稳",
                "age_range": "middle_age",
            },
        ],
        "expected_conflict": False,
        "reason": "时间跨度大，自然变化",
    },
    {
        "id": "hn_ownership_transfer",
        "description": "物品归属转移 - 不冲突",
        "project_id": "proj_test",
        "timeline_id": "main",
        "claims": [
            {
                "subject": "玉佩",
                "predicate": "owned_by",
                "object_entity": "沈砚",
                "story_order": 50.0,
                "chapter_id": "ch_03",
                "body_rev": 1,
                "paragraph_id": "p_3_05",
                "context": "沈砚佩戴玉佩",
            },
            {
                "subject": "玉佩",
                "predicate": "owned_by",
                "object_entity": "苏清",
                "story_order": 80.0,
                "chapter_id": "ch_05",
                "body_rev": 1,
                "paragraph_id": "p_5_10",
                "context": "沈砚将玉佩赠予苏清",
                "transfer_event": "gift",
            },
        ],
        "expected_conflict": False,
        "reason": "有转移事件，时间先后明确",
    },
]

EASY_NEGATIVES = [
    {
        "id": "en_different_entities",
        "description": "完全不同的实体 - 不冲突",
        "claims": [
            {"subject": "沈砚", "predicate": "alive", "object_value": "true"},
            {"subject": "苏清", "predicate": "location", "object_value": "京城"},
        ],
        "expected_conflict": False,
        "reason": "主体不同，谓词不同",
    },
    {
        "id": "en_different_timelines",
        "description": "不同时间线 - 不冲突",
        "claims": [
            {"subject": "沈砚", "predicate": "alive", "timeline_id": "main", "story_order": 100.0},
            {"subject": "沈砚", "predicate": "alive", "timeline_id": "flashback", "story_order": 100.0},
        ],
        "expected_conflict": False,
        "reason": "时间线不同",
    },
]


def get_all_fixtures():
    """获取所有测试夹具"""
    return {
        "positive": POSITIVE_CASES,
        "hard_negative": HARD_NEGATIVES,
        "easy_negative": EASY_NEGATIVES,
    }


def get_positive_count():
    """获取正例数量"""
    return len(POSITIVE_CASES)


def get_hard_negative_count():
    """获取 hard negative 数量"""
    return len(HARD_NEGATIVES)

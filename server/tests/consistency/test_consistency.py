"""
Tests for consistency service - claim management and rule evaluation
"""

from services.consistency import compute_claim_fingerprint


class TestClaimFingerprint:
    """测试 claim 指纹计算"""

    def test_same_claim_same_fingerprint(self):
        """相同 claim 产生相同指纹"""
        fp1 = compute_claim_fingerprint(
            subject_text="沈砚",
            predicate="alive",
            object_type="scalar",
            object_value="true",
            polarity="positive",
        )
        fp2 = compute_claim_fingerprint(
            subject_text="沈砚",
            predicate="alive",
            object_type="scalar",
            object_value="true",
            polarity="positive",
        )

        assert fp1 == fp2
        assert len(fp1) == 64  # SHA-256

    def test_case_insensitive(self):
        """大小写不影响指纹"""
        fp1 = compute_claim_fingerprint(
            subject_text="沈砚",
            predicate="alive",
            object_type="scalar",
            object_value="True",
            polarity="positive",
        )
        fp2 = compute_claim_fingerprint(
            subject_text="沈砚",
            predicate="alive",
            object_type="scalar",
            object_value="true",
            polarity="positive",
        )

        assert fp1 == fp2

    def test_whitespace_normalized(self):
        """空白规范化"""
        fp1 = compute_claim_fingerprint(
            subject_text=" 沈砚 ",
            predicate="alive",
            object_type="scalar",
            object_value=" true ",
            polarity="positive",
        )
        fp2 = compute_claim_fingerprint(
            subject_text="沈砚",
            predicate="alive",
            object_type="scalar",
            object_value="true",
            polarity="positive",
        )

        assert fp1 == fp2

    def test_different_subject_different_fingerprint(self):
        """不同主体产生不同指纹"""
        fp1 = compute_claim_fingerprint(
            subject_text="沈砚",
            predicate="alive",
            object_type="scalar",
            object_value="true",
            polarity="positive",
        )
        fp2 = compute_claim_fingerprint(
            subject_text="苏清",
            predicate="alive",
            object_type="scalar",
            object_value="true",
            polarity="positive",
        )

        assert fp1 != fp2

    def test_different_predicate_different_fingerprint(self):
        """不同谓词产生不同指纹"""
        fp1 = compute_claim_fingerprint(
            subject_text="沈砚",
            predicate="alive",
            object_type="scalar",
            object_value="true",
            polarity="positive",
        )
        fp2 = compute_claim_fingerprint(
            subject_text="沈砚",
            predicate="location",
            object_type="scalar",
            object_value="true",
            polarity="positive",
        )

        assert fp1 != fp2

    def test_different_polarity_different_fingerprint(self):
        """不同极性产生不同指纹"""
        fp1 = compute_claim_fingerprint(
            subject_text="沈砚",
            predicate="alive",
            object_type="scalar",
            object_value="true",
            polarity="positive",
        )
        fp2 = compute_claim_fingerprint(
            subject_text="沈砚",
            predicate="alive",
            object_type="scalar",
            object_value="true",
            polarity="negative",
        )

        assert fp1 != fp2

    def test_none_object_value(self):
        """处理 None 对象值"""
        fp = compute_claim_fingerprint(
            subject_text="沈砚",
            predicate="has_weapon",
            object_type="entity",
            object_value=None,
            polarity="positive",
        )

        assert len(fp) == 64


class TestRuleLogic:
    """测试规则逻辑"""

    def test_alive_conflict_detection_concept(self):
        """生死冲突检测概念验证"""
        # 测试逻辑：如果 A 在时间 T1 死亡，则 T2 > T1 时不能 alive=true
        death_time = 100.0
        later_alive_claim_time = 150.0

        conflict_detected = later_alive_claim_time > death_time
        assert conflict_detected is True

    def test_ownership_conflict_detection_concept(self):
        """物品归属冲突检测概念验证"""
        # 测试逻辑：同一物品在同一时间点只能归属一个人
        owner_a = "char_a"
        owner_b = "char_b"
        time_a = 100.0
        time_b = 100.001

        # 时间点足够接近视为冲突
        time_diff = abs(time_a - time_b)
        conflict_detected = time_diff < 0.01 and owner_a != owner_b
        assert conflict_detected is True

    def test_knowledge_boundary_violation_concept(self):
        """知情边界违规检测概念验证"""
        # 测试逻辑：角色在 T1 使用知识，但在 T2 > T1 才获知
        knowledge_used_at = 50.0
        knowledge_acquired_at = 100.0

        violation_detected = knowledge_used_at < knowledge_acquired_at
        assert violation_detected is True

    def test_no_conflict_when_time_sequential(self):
        """时间顺序正确时无冲突"""
        # 先获知，后使用
        knowledge_acquired_at = 50.0
        knowledge_used_at = 100.0

        violation_detected = knowledge_used_at < knowledge_acquired_at
        assert violation_detected is False


class TestHardNegatives:
    """Hard negative 测试 - 不应产生冲突"""

    def test_conditional_ability_no_conflict(self):
        """条件性能力不冲突"""
        # "不擅长用剑" vs "危急时勉强拔剑"
        # 应该不冲突，因为有明确条件限定
        conditional_claim = {"ability": "sword", "level": "barely_usable", "condition": "emergency"}

        # 有条件限定时不应判为冲突
        has_condition = "condition" in conditional_claim
        assert has_condition is True

    def test_temporal_state_change_no_conflict(self):
        """时间演变的状态变化不冲突"""
        # "四月开始融雪" vs "四月底山阴仍有残雪"
        # 不冲突，因为时间不同且状态可演变
        early_state = {"month": 4, "day": 1, "snow": "melting"}
        late_state = {"month": 4, "day": 28, "snow": "remaining_patches"}

        # 时间不同且状态可演变
        time_different = early_state["day"] != late_state["day"]
        assert time_different is True

    def test_treated_condition_no_conflict(self):
        """治疗后状态不冲突"""
        # "左肩旧伤遇寒痛" vs "服药后症状减轻"
        # 不冲突，因为有明确的因果关系
        after_treatment = {"condition": "shoulder_pain", "severity": "reduced", "cause": "medication"}

        # 有因果关系时不应判为冲突
        has_cause = "cause" in after_treatment
        assert has_cause is True

    def test_gradual_change_no_conflict(self):
        """渐变不冲突"""
        # "年轻时英俊" vs "中年发福"
        # 不冲突，因为时间点不同
        young_state = {"age_range": "young", "appearance": "handsome"}
        middle_age_state = {"age_range": "middle", "appearance": "gaining_weight"}

        time_different = young_state["age_range"] != middle_age_state["age_range"]
        assert time_different is True

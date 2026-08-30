"""
Tests for RuleScanner - real scanner invocations with database
"""
import pytest
from sqlalchemy import select

from db import Chapter, ChapterBody, ConsistencyClaim, ConsistencyRun, GuardIssue
from services.rule_scanner import RuleScanner


@pytest.mark.asyncio
async def test_scanner_alive_conflict_detection(async_db_session):
    """Test alive conflict detection with real scanner"""
    project_id = "proj_test"
    chapter_id = "ch_test_alive"
    body_rev = 1

    # Create test chapter
    chapter = Chapter(
        id=chapter_id,
        project_id=project_id,
        volume_id=None,
        title="Test Chapter",
        idx=1,
        words=100,
    )
    async_db_session.add(chapter)

    # Create chapter body
    body = ChapterBody(
        chapter_id=chapter_id,
        rev=body_rev,
        content_html="<p>Test content</p>",
        content_json={},
        content_hash="test_hash",
    )
    async_db_session.add(body)

    # Create consistency run
    run = ConsistencyRun(
        project_id=project_id,
        chapter_id=chapter_id,
        body_rev=body_rev,
        status="pending",
    )
    async_db_session.add(run)
    await async_db_session.commit()
    await async_db_session.refresh(run)

    # Create claims that will trigger alive conflict
    # Claim 1: Character dies at timeline 100
    claim1 = ConsistencyClaim(
        chapter_id=chapter_id,
        body_rev=body_rev,
        source_kind="body",
        claim_type="character_state",
        text="Character A dies",
        structured_data={
            "character": "Character A",
            "attribute": "alive",
            "value": "false",
            "timeline_ref": "100.0",
        },
        confidence=0.95,
        extractor_version="1.0.0",
        fingerprint="fp_death",
        status="accepted",
    )
    async_db_session.add(claim1)

    # Claim 2: Character alive at timeline 150 (conflict!)
    claim2 = ConsistencyClaim(
        chapter_id=chapter_id,
        body_rev=body_rev,
        source_kind="body",
        claim_type="character_state",
        text="Character A is alive",
        structured_data={
            "character": "Character A",
            "attribute": "alive",
            "value": "true",
            "timeline_ref": "150.0",
        },
        confidence=0.90,
        extractor_version="1.0.0",
        fingerprint="fp_alive",
        status="accepted",
    )
    async_db_session.add(claim2)
    await async_db_session.commit()

    # Run scanner
    scanner = RuleScanner(rule_version="1.0.0")
    issues = await scanner.scan_chapter(
        db=async_db_session,
        run_id=run.id,
        project_id=project_id,
        chapter_id=chapter_id,
        body_rev=body_rev,
    )

    # Verify conflict detected
    assert len(issues) > 0
    alive_conflicts = [i for i in issues if i["issue_type"] == "alive_conflict"]
    assert len(alive_conflicts) == 1

    # Verify issue persisted
    result = await async_db_session.execute(
        select(GuardIssue).where(
            GuardIssue.project_id == project_id,
            GuardIssue.chapter_id == chapter_id,
        )
    )
    persisted_issues = result.scalars().all()
    assert len(persisted_issues) >= 1


@pytest.mark.asyncio
async def test_scanner_ownership_conflict_detection(async_db_session):
    """Test ownership conflict detection"""
    project_id = "proj_test"
    chapter_id = "ch_test_ownership"
    body_rev = 1

    chapter = Chapter(
        id=chapter_id,
        project_id=project_id,
        volume_id=None,
        title="Test Chapter",
        idx=1,
        words=100,
    )
    async_db_session.add(chapter)

    body = ChapterBody(
        chapter_id=chapter_id,
        rev=body_rev,
        content_html="<p>Test</p>",
        content_json={},
        content_hash="hash",
    )
    async_db_session.add(body)

    run = ConsistencyRun(
        project_id=project_id,
        chapter_id=chapter_id,
        body_rev=body_rev,
        status="pending",
    )
    async_db_session.add(run)
    await async_db_session.commit()
    await async_db_session.refresh(run)

    # Two characters own same item at same time
    claim1 = ConsistencyClaim(
        chapter_id=chapter_id,
        body_rev=body_rev,
        source_kind="body",
        claim_type="relationship",
        text="Character A owns sword",
        structured_data={
            "subject": "Character A",
            "predicate": "owns",
            "object": "Sword X",
            "timeline_ref": "100.0",
        },
        confidence=0.95,
        extractor_version="1.0.0",
        fingerprint="fp_own1",
        status="accepted",
    )
    async_db_session.add(claim1)

    claim2 = ConsistencyClaim(
        chapter_id=chapter_id,
        body_rev=body_rev,
        source_kind="body",
        claim_type="relationship",
        text="Character B owns sword",
        structured_data={
            "subject": "Character B",
            "predicate": "owns",
            "object": "Sword X",
            "timeline_ref": "100.001",
        },
        confidence=0.90,
        extractor_version="1.0.0",
        fingerprint="fp_own2",
        status="accepted",
    )
    async_db_session.add(claim2)
    await async_db_session.commit()

    scanner = RuleScanner(rule_version="1.0.0")
    issues = await scanner.scan_chapter(
        db=async_db_session,
        run_id=run.id,
        project_id=project_id,
        chapter_id=chapter_id,
        body_rev=body_rev,
    )

    ownership_conflicts = [i for i in issues if i["issue_type"] == "ownership_conflict"]
    assert len(ownership_conflicts) >= 1


@pytest.mark.asyncio
async def test_scanner_knowledge_boundary_violation(async_db_session):
    """Test knowledge boundary violation detection"""
    project_id = "proj_test"
    chapter_id = "ch_test_knowledge"
    body_rev = 1

    chapter = Chapter(
        id=chapter_id,
        project_id=project_id,
        volume_id=None,
        title="Test Chapter",
        idx=1,
        words=100,
    )
    async_db_session.add(chapter)

    body = ChapterBody(
        chapter_id=chapter_id,
        rev=body_rev,
        content_html="<p>Test</p>",
        content_json={},
        content_hash="hash",
    )
    async_db_session.add(body)

    run = ConsistencyRun(
        project_id=project_id,
        chapter_id=chapter_id,
        body_rev=body_rev,
        status="pending",
    )
    async_db_session.add(run)
    await async_db_session.commit()
    await async_db_session.refresh(run)

    # Character uses knowledge before acquiring it
    claim1 = ConsistencyClaim(
        chapter_id=chapter_id,
        body_rev=body_rev,
        source_kind="body",
        claim_type="event",
        text="Character uses secret code",
        structured_data={
            "character": "Character A",
            "action": "uses_knowledge",
            "knowledge": "Secret Code",
            "timeline_ref": "50.0",
        },
        confidence=0.95,
        extractor_version="1.0.0",
        fingerprint="fp_use",
        status="accepted",
    )
    async_db_session.add(claim1)

    claim2 = ConsistencyClaim(
        chapter_id=chapter_id,
        body_rev=body_rev,
        source_kind="body",
        claim_type="event",
        text="Character learns secret code",
        structured_data={
            "character": "Character A",
            "action": "acquires_knowledge",
            "knowledge": "Secret Code",
            "timeline_ref": "100.0",
        },
        confidence=0.90,
        extractor_version="1.0.0",
        fingerprint="fp_acquire",
        status="accepted",
    )
    async_db_session.add(claim2)
    await async_db_session.commit()

    scanner = RuleScanner(rule_version="1.0.0")
    issues = await scanner.scan_chapter(
        db=async_db_session,
        run_id=run.id,
        project_id=project_id,
        chapter_id=chapter_id,
        body_rev=body_rev,
    )

    knowledge_violations = [i for i in issues if i["issue_type"] == "knowledge_boundary"]
    assert len(knowledge_violations) >= 1


@pytest.mark.asyncio
async def test_scanner_skips_rules_when_timeline_unknown(async_db_session):
    """Test that scanner skips timeline-based rules when story_order is None"""
    project_id = "proj_test"
    chapter_id = "ch_test_no_timeline"
    body_rev = 1

    chapter = Chapter(
        id=chapter_id,
        project_id=project_id,
        volume_id=None,
        title="Test Chapter",
        idx=1,
        words=100,
    )
    async_db_session.add(chapter)

    body = ChapterBody(
        chapter_id=chapter_id,
        rev=body_rev,
        content_html="<p>Test</p>",
        content_json={},
        content_hash="hash",
    )
    async_db_session.add(body)

    run = ConsistencyRun(
        project_id=project_id,
        chapter_id=chapter_id,
        body_rev=body_rev,
        status="pending",
    )
    async_db_session.add(run)
    await async_db_session.commit()
    await async_db_session.refresh(run)

    # Claims without timeline_ref (or None)
    claim1 = ConsistencyClaim(
        chapter_id=chapter_id,
        body_rev=body_rev,
        source_kind="body",
        claim_type="character_state",
        text="Character dies",
        structured_data={
            "character": "Character A",
            "attribute": "alive",
            "value": "false",
        },
        confidence=0.95,
        extractor_version="1.0.0",
        fingerprint="fp_no_time1",
        status="accepted",
    )
    async_db_session.add(claim1)

    claim2 = ConsistencyClaim(
        chapter_id=chapter_id,
        body_rev=body_rev,
        source_kind="body",
        claim_type="character_state",
        text="Character alive",
        structured_data={
            "character": "Character A",
            "attribute": "alive",
            "value": "true",
        },
        confidence=0.90,
        extractor_version="1.0.0",
        fingerprint="fp_no_time2",
        status="accepted",
    )
    async_db_session.add(claim2)
    await async_db_session.commit()

    scanner = RuleScanner(rule_version="1.0.0")
    issues = await scanner.scan_chapter(
        db=async_db_session,
        run_id=run.id,
        project_id=project_id,
        chapter_id=chapter_id,
        body_rev=body_rev,
    )

    # Should not detect issues when timeline is unknown
    assert len(issues) == 0


@pytest.mark.asyncio
async def test_scanner_marks_stale_issues(async_db_session):
    """Test that scanner marks previously detected issues as stale if not rediscovered"""
    project_id = "proj_test"
    chapter_id = "ch_test_stale"
    body_rev = 1

    chapter = Chapter(
        id=chapter_id,
        project_id=project_id,
        volume_id=None,
        title="Test Chapter",
        idx=1,
        words=100,
    )
    async_db_session.add(chapter)

    body = ChapterBody(
        chapter_id=chapter_id,
        rev=body_rev,
        content_html="<p>Test</p>",
        content_json={},
        content_hash="hash",
    )
    async_db_session.add(body)

    run1 = ConsistencyRun(
        project_id=project_id,
        chapter_id=chapter_id,
        body_rev=body_rev,
        status="pending",
    )
    async_db_session.add(run1)
    await async_db_session.commit()
    await async_db_session.refresh(run1)

    # Create an old issue from previous run
    old_issue = GuardIssue(
        id="gi_old",
        run_id=run1.id,
        project_id=project_id,
        chapter_id=chapter_id,
        issue_type="alive_conflict",
        severity="high",
        message="Old conflict",
        rule_version="1.0.0",
        fingerprint="fp_old_conflict",
        status="open",
        confidence=0.9,
        issue_rev=1,
    )
    async_db_session.add(old_issue)
    await async_db_session.commit()

    # Run scanner again with no conflicting claims
    run2 = ConsistencyRun(
        project_id=project_id,
        chapter_id=chapter_id,
        body_rev=body_rev,
        status="pending",
    )
    async_db_session.add(run2)
    await async_db_session.commit()
    await async_db_session.refresh(run2)

    scanner = RuleScanner(rule_version="1.0.0")
    await scanner.scan_chapter(
        db=async_db_session,
        run_id=run2.id,
        project_id=project_id,
        chapter_id=chapter_id,
        body_rev=body_rev,
    )

    # Check old issue is now stale
    await async_db_session.refresh(old_issue)
    assert old_issue.status == "stale"
    assert old_issue.stale_at is not None

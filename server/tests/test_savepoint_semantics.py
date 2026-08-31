"""SQLite 测试夹具的 SAVEPOINT 语义必须和生产（asyncpg）一致。

为什么这组测试必须存在：`services.consistency.get_or_create_run`、
`upsert_active_summary` 和 `services.rule_scanner.RuleScanner._upsert_issue` 都用
`db.begin_nested()`（SAVEPOINT）兜唯一键并发竞态。pysqlite 默认把 BEGIN 推迟到第一条
DML，而 SAVEPOINT 不算 DML —— 于是 `SAVEPOINT` 语句自己开启了 DBAPI 事务，
`RELEASE SAVEPOINT` 把它一并提交。结果是：

* 嵌套事务里写的行**立刻持久化**，外层 rollback 再也回滚不掉；
* 「过期结论必须随事务回滚」（tasks.fail_run 的前置 rollback）在 SQLite 上会给出
  假绿 —— 或者反过来，修好之后才暴露出真实行为。

夹具用 `isolation_level = None` + 显式 BEGIN 接管事务控制来对齐语义。这里直接钉住
这个语义，而不是只靠某条管道测试间接体现：夹具被改回去时，失败信息应该指向夹具。
"""
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from db.models_guard import GuardIssue


def make_issue(issue_id: str, *, fingerprint: str, run_id: int) -> GuardIssue:
    return GuardIssue(
        id=issue_id,
        project_id="proj_a",
        chapter_id="ch_a",
        run_id=run_id,
        issue_type="alive_conflict",
        rule_version="1.0.0",
        fingerprint=fingerprint,
        severity="high",
        confidence=0.9,
        description="用于验证 SAVEPOINT 语义",
        evidence={},
        anchor={},
        actions=["false_positive"],
        status="open",
        issue_rev=1,
        resolved=False,
        false_positive=False,
    )


async def count_issues(db) -> int:
    return (await db.execute(select(func.count()).select_from(GuardIssue))).scalar_one()


async def make_run_row(db, make_run) -> int:
    """建一个 run 并返回它的 id（GuardIssue.run_id 是 NOT NULL 外键）。"""
    run = make_run(project_id="proj_a", chapter_id="ch_a")
    db.add(run)
    await db.flush()
    return run.id


async def test_a_released_savepoint_is_still_undone_by_the_outer_rollback(
    async_db_session, seed_project, make_run
):
    """SAVEPOINT 提交后，外层 rollback 仍然要能撤掉它写的行。

    复现的是 _scan_rules_async 的真实序列：先 commit（前一步的产出），再 SELECT
    （加载 claim），然后在 SAVEPOINT 里写 issue，最后发现正文版本已推进而回滚。
    pysqlite 的隐式 BEGIN 会让这些 issue 留在库里 —— 正是「旧 worker 覆盖新版本」
    那一类缺陷。
    """
    await seed_project()
    run_id = await make_run_row(async_db_session, make_run)
    await async_db_session.commit()

    # SELECT 不触发 pysqlite 的隐式 BEGIN，SAVEPOINT 因此成了事务的起点
    await count_issues(async_db_session)

    async with async_db_session.begin_nested():
        async_db_session.add(make_issue("gi_sp", fingerprint="fp_sp", run_id=run_id))
        await async_db_session.flush()

    await async_db_session.rollback()

    assert await count_issues(async_db_session) == 0, (
        "SAVEPOINT 里写的行在外层 rollback 之后仍然存在：测试夹具的事务语义与生产不一致"
    )


async def test_a_savepoint_rollback_recovers_from_a_unique_violation(
    async_db_session, seed_project, make_run
):
    """撞唯一键后回滚 SAVEPOINT，外层事务必须还能继续写。

    这是 _upsert_issue / get_or_create_run 处理并发竞态的前提：撞键只能废掉那一条
    语句，本次运行已经写好的其他行不能跟着丢。
    """
    await seed_project()
    run_id = await make_run_row(async_db_session, make_run)
    async_db_session.add(make_issue("gi_first", fingerprint="fp_dup", run_id=run_id))
    await async_db_session.flush()

    try:
        async with async_db_session.begin_nested():
            # uq_guard_issue_fingerprint(project_id, fingerprint) 冲突
            async_db_session.add(
                make_issue("gi_second", fingerprint="fp_dup", run_id=run_id)
            )
            await async_db_session.flush()
    except IntegrityError:
        pass
    else:
        raise AssertionError("重复 fingerprint 没有触发 IntegrityError，唯一约束丢了")

    # 外层事务仍然可用
    async_db_session.add(make_issue("gi_third", fingerprint="fp_other", run_id=run_id))
    await async_db_session.flush()

    ids = sorted(
        (await async_db_session.execute(select(GuardIssue.id))).scalars().all()
    )
    assert ids == ["gi_first", "gi_third"]


async def test_the_unique_fingerprint_constraint_exists_on_sqlite(async_db_session):
    """夹具建表时确实带上了 uq_guard_issue_fingerprint。

    它是普通唯一约束（不是 postgresql_where 部分索引），所以不在
    POSTGRES_ONLY_INDEXES 里，SQLite 上必须真实存在 —— 否则上面那条竞态测试
    会因为「根本没有约束」而假绿。
    """
    from sqlalchemy import inspect

    def _constraints(sync_conn):
        inspector = inspect(sync_conn)
        names = {c["name"] for c in inspector.get_unique_constraints("guard_issues")}
        names |= {i["name"] for i in inspector.get_indexes("guard_issues") if i["unique"]}
        return names

    names = await async_db_session.run_sync(lambda s: _constraints(s.connection()))
    assert "uq_guard_issue_fingerprint" in names

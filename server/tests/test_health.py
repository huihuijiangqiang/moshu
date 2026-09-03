from types import SimpleNamespace

import pytest

import main


class _Connection:
    def __init__(self, migration_version=main.MIGRATION_HEAD):
        self.migration_version = migration_version

    async def execute(self, _statement):
        return SimpleNamespace(scalar_one_or_none=lambda: self.migration_version)


class _Engine:
    def __init__(self, migration_version=main.MIGRATION_HEAD):
        self.migration_version = migration_version

    def connect(self):
        engine = self

        class _Context:
            async def __aenter__(self):
                if getattr(engine, "fail", False):
                    raise RuntimeError("database unavailable")
                return _Connection(engine.migration_version)

            async def __aexit__(self, *_args):
                return False

        return _Context()


class _Redis:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.closed = False

    async def ping(self):
        if self.fail:
            raise RuntimeError("redis unavailable")

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_readiness_reports_all_dependencies_ready(monkeypatch):
    fake_redis = _Redis()
    monkeypatch.setattr(main, "engine", _Engine())
    monkeypatch.setattr(main, "redis", SimpleNamespace(from_url=lambda *_args, **_kwargs: fake_redis))

    response = await main.readiness()

    assert response.status_code == 200
    assert response.status_code == 200
    assert b'"status":"ready"' in response.body
    assert b'"migrations":"ok"' in response.body
    assert fake_redis.closed is True


@pytest.mark.asyncio
async def test_readiness_returns_503_without_leaking_dependency_errors(monkeypatch):
    fake_redis = _Redis(fail=True)
    database = _Engine()
    database.fail = True
    monkeypatch.setattr(main, "engine", database)
    monkeypatch.setattr(main, "redis", SimpleNamespace(from_url=lambda *_args, **_kwargs: fake_redis))

    response = await main.readiness()

    assert response.status_code == 503
    assert b'"status":"not_ready"' in response.body
    assert b'"postgres":"failed"' in response.body
    assert b'"migrations":"unknown"' in response.body
    assert b'"redis":"failed"' in response.body
    assert b"unavailable" not in response.body
    assert fake_redis.closed is True


@pytest.mark.asyncio
async def test_readiness_handles_invalid_redis_configuration(monkeypatch):
    monkeypatch.setattr(main, "engine", _Engine())

    def invalid_url(*_args, **_kwargs):
        raise ValueError("invalid redis URL")

    monkeypatch.setattr(main, "redis", SimpleNamespace(from_url=invalid_url))

    response = await main.readiness()

    assert response.status_code == 503
    assert b'"status":"not_ready"' in response.body
    assert b'"postgres":"ok"' in response.body
    assert b'"migrations":"ok"' in response.body
    assert b'"redis":"failed"' in response.body


@pytest.mark.asyncio
async def test_readiness_rejects_database_behind_migration_head(monkeypatch):
    monkeypatch.setattr(main, "engine", _Engine("010_claim_temporal_evidence"))
    monkeypatch.setattr(
        main,
        "redis",
        SimpleNamespace(from_url=lambda *_args, **_kwargs: _Redis()),
    )

    response = await main.readiness()

    assert response.status_code == 503
    assert b'"postgres":"ok"' in response.body
    assert b'"migrations":"outdated"' in response.body

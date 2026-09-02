from types import SimpleNamespace

import pytest

import main


class _Connection:
    async def execute(self, _statement):
        return None


class _Engine:
    def connect(self):
        engine = self

        class _Context:
            async def __aenter__(self):
                if getattr(engine, "fail", False):
                    raise RuntimeError("database unavailable")
                return _Connection()

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
    assert response.body == b'{"status":"ready","checks":{"postgres":"ok","redis":"ok"}}'
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
    assert response.body == b'{"status":"not_ready","checks":{"postgres":"failed","redis":"failed"}}'
    assert b"unavailable" not in response.body
    assert fake_redis.closed is True

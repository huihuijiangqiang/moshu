"""Authentication abuse-protection tests (no external Redis or Turnstile calls)."""

import pytest

from api import auth
from config import settings


class FakeRedis:
    def __init__(self):
        self.values: dict[str, str] = {}
        self.expiry: dict[str, int] = {}

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, *, ex=None, nx=False):
        if nx and key in self.values:
            return None
        self.values[key] = value
        if ex is not None:
            self.expiry[key] = ex
        return True

    async def delete(self, *keys):
        for key in keys:
            self.values.pop(key, None)

    async def eval(self, script, numkeys, key, *args):
        if "GET" in script:
            value = self.values.pop(key, None)
            return value
        value = int(self.values.get(key, "0")) + 1
        self.values[key] = str(value)
        return value

    async def aclose(self):
        return None


@pytest.mark.asyncio
async def test_captcha_config_is_off_by_default(app_client):
    response = await app_client.get("/auth/captcha/config")
    assert response.status_code == 200
    assert response.json() == {"mode": "off", "site_key": None, "challenge_required": False}


@pytest.mark.asyncio
async def test_always_mode_rejects_auth_without_captcha(app_client, monkeypatch):
    monkeypatch.setattr(settings, "auth_captcha_mode", "always")
    response = await app_client.post(
        "/auth/register",
        json={"name": "作者", "email": "captcha@example.test", "password": "long-enough-password"},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "CAPTCHA_REQUIRED"


@pytest.mark.asyncio
async def test_challenge_is_single_use(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(auth, "redis", type("RedisModule", (), {"from_url": lambda *a, **k: fake}))
    monkeypatch.setattr(settings, "auth_captcha_mode", "always")
    monkeypatch.setattr(settings, "auth_captcha_site_key", "site-key")
    monkeypatch.setattr(settings, "auth_captcha_secret_key", "secret-key")

    challenge = await auth.captcha_challenge()
    assert await auth._consume_captcha_challenge(challenge.challenge_id)
    assert not await auth._consume_captcha_challenge(challenge.challenge_id)


@pytest.mark.asyncio
async def test_failure_counter_is_atomic_script(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(auth, "redis", type("RedisModule", (), {"from_url": lambda *a, **k: fake}))
    await auth._record_failure("email:one@example.test")
    await auth._record_failure("email:one@example.test")
    assert await auth._failure_count("email:one@example.test") == 2

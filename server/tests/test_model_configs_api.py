from sqlalchemy import select

from api.model_configs import get_model_config_probe
from db.models_core import User
from db.models_model_config import UserModelConfig
from main import app
from services.model_configs import (
    CredentialDecryptionError,
    InvalidModelEndpointError,
    ProbeResult,
    assert_public_endpoint_resolution,
    decrypt_api_key,
    encrypt_api_key,
    normalize_public_base_url,
)


class SuccessfulProbe:
    async def test(self, config):
        assert decrypt_api_key(
            config.api_key_ciphertext, user_id=config.user_id, config_id=config.id
        ) == "sk-personal-secret"
        return ProbeResult(True, "ok")


async def _users(async_db_session):
    async_db_session.add_all(
        [
            User(
                id="model_owner",
                name="Model Owner",
                email="model-owner@example.test",
                quota_remaining=100,
                quota_total=100,
            ),
            User(
                id="model_other",
                name="Other User",
                email="model-other@example.test",
                quota_remaining=100,
                quota_total=100,
            ),
        ]
    )
    await async_db_session.commit()


async def test_model_config_is_encrypted_masked_isolated_and_versioned(
    app_client, async_db_session, auth_headers
):
    await _users(async_db_session)
    empty = await app_client.get("/account/model-config", headers=auth_headers("model_owner"))
    assert empty.status_code == 200
    assert empty.json()["configured"] is False

    created = await app_client.put(
        "/account/model-config",
        headers=auth_headers("model_owner"),
        json={
            "providerName": "我的中转站",
            "baseUrl": "https://gateway.example.com/v1/",
            "model": "novel-model",
            "apiKey": "sk-personal-secret",
            "enabled": True,
            "revision": 0,
        },
    )
    assert created.status_code == 200
    payload = created.json()
    assert payload["baseUrl"] == "https://gateway.example.com/v1"
    assert payload["keyHint"] == "sk-****cret"
    assert payload["revision"] == 1
    assert "sk-personal-secret" not in created.text

    stored = (await async_db_session.execute(select(UserModelConfig))).scalar_one()
    assert stored.api_key_ciphertext != "sk-personal-secret"
    assert "sk-personal-secret" not in stored.api_key_ciphertext
    assert decrypt_api_key(
        stored.api_key_ciphertext, user_id="model_owner", config_id=stored.id
    ) == "sk-personal-secret"

    isolated = await app_client.get("/account/model-config", headers=auth_headers("model_other"))
    assert isolated.json()["configured"] is False

    updated = await app_client.put(
        "/account/model-config",
        headers=auth_headers("model_owner"),
        json={
            "providerName": "我的中转站",
            "baseUrl": "https://gateway.example.com/v1",
            "model": "novel-model-v2",
            "enabled": False,
            "revision": 1,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["revision"] == 2
    assert updated.json()["keyHint"] == "sk-****cret"
    await async_db_session.refresh(stored)
    assert decrypt_api_key(
        stored.api_key_ciphertext, user_id="model_owner", config_id=stored.id
    ) == "sk-personal-secret"

    conflict = await app_client.put(
        "/account/model-config",
        headers=auth_headers("model_owner"),
        json={
            "providerName": "stale",
            "baseUrl": "https://gateway.example.com/v1",
            "model": "stale-model",
            "revision": 1,
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["currentRevision"] == 2


async def test_model_config_rejects_non_public_or_credentialed_endpoints(
    app_client, async_db_session, auth_headers
):
    await _users(async_db_session)
    for endpoint in (
        "http://gateway.example.com/v1",
        "https://127.0.0.1/v1",
        "https://10.0.0.2/v1",
        "https://user:pass@gateway.example.com/v1",
        "https://gateway.example.com/v1?key=secret",
    ):
        response = await app_client.put(
            "/account/model-config",
            headers=auth_headers("model_owner"),
            json={
                "providerName": "unsafe",
                "baseUrl": endpoint,
                "model": "model",
                "apiKey": "12345678",
                "revision": 0,
            },
        )
        assert response.status_code == 422, endpoint
        assert response.json()["detail"]["code"] == "MODEL_ENDPOINT_NOT_ALLOWED"


async def test_model_config_probe_and_delete_use_current_revision(
    app_client, async_db_session, auth_headers
):
    await _users(async_db_session)
    created = await app_client.put(
        "/account/model-config",
        headers=auth_headers("model_owner"),
        json={
            "providerName": "provider",
            "baseUrl": "https://gateway.example.com/v1",
            "model": "model",
            "apiKey": "sk-personal-secret",
            "revision": 0,
        },
    )
    app.dependency_overrides[get_model_config_probe] = lambda: SuccessfulProbe()
    try:
        tested = await app_client.post(
            "/account/model-config/test",
            headers=auth_headers("model_owner"),
            json={"revision": created.json()["revision"]},
        )
    finally:
        app.dependency_overrides.pop(get_model_config_probe, None)
    assert tested.status_code == 200
    assert tested.json()["lastTestStatus"] == "ok"
    assert tested.json()["lastErrorCode"] is None
    assert "sk-personal-secret" not in tested.text

    stale = await app_client.delete(
        "/account/model-config?revision=99", headers=auth_headers("model_owner")
    )
    assert stale.status_code == 409
    deleted = await app_client.delete(
        f"/account/model-config?revision={created.json()['revision']}",
        headers=auth_headers("model_owner"),
    )
    assert deleted.status_code == 204
    assert (await async_db_session.execute(select(UserModelConfig))).scalars().all() == []


def test_credential_cipher_binds_secret_to_owner_and_config():
    encrypted = encrypt_api_key("sk-secret-value", user_id="owner", config_id="config")
    assert decrypt_api_key(encrypted, user_id="owner", config_id="config") == "sk-secret-value"
    try:
        decrypt_api_key(encrypted, user_id="other", config_id="config")
    except CredentialDecryptionError:
        pass
    else:
        raise AssertionError("credential decrypted for the wrong owner")


def test_public_endpoint_normalization_keeps_path_and_removes_trailing_slash():
    assert normalize_public_base_url(" https://API.Example.com:8443/openai/v1/ ") == (
        "https://api.example.com:8443/openai/v1"
    )


async def test_endpoint_resolution_rejects_dns_answers_in_private_networks(monkeypatch):
    monkeypatch.setattr(
        "services.model_configs.socket.getaddrinfo",
        lambda *args, **kwargs: [(2, 1, 6, "", ("10.20.30.40", 443))],
    )
    try:
        await assert_public_endpoint_resolution("https://gateway.example.com/v1")
    except InvalidModelEndpointError:
        pass
    else:
        raise AssertionError("private DNS answer was accepted")

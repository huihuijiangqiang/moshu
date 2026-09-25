import pytest
from pydantic import ValidationError

from config import Settings, validate_public_deployment_secrets


def test_localhost_allows_frictionless_development_defaults():
    validate_public_deployment_secrets(
        public_app_url="http://localhost:8080",
        cors_origins="http://localhost:8080",
        jwt_secret_key="local-development-only-change-before-production",
        credential_encryption_key=None,
    )


def test_public_origin_rejects_example_jwt_secret():
    with pytest.raises(ValueError, match="JWT_SECRET_KEY"):
        validate_public_deployment_secrets(
            public_app_url="https://write.example.com",
            cors_origins="https://write.example.com",
            jwt_secret_key="local-development-only-change-before-production",
            credential_encryption_key="a" * 48,
        )


def test_public_origin_requires_separate_credential_key():
    with pytest.raises(ValueError, match="CREDENTIAL_ENCRYPTION_KEY"):
        validate_public_deployment_secrets(
            public_app_url="https://write.example.com",
            cors_origins="https://write.example.com",
            jwt_secret_key="j" * 48,
            credential_encryption_key=None,
        )


def test_public_origin_accepts_independent_random_secrets():
    validate_public_deployment_secrets(
        public_app_url="https://write.example.com",
        cors_origins="https://write.example.com",
        jwt_secret_key="j" * 48,
        credential_encryption_key="c" * 48,
    )


@pytest.mark.parametrize("origin", ["https://write.example.com", "*", "https://localhost.evil.example"])
def test_cors_alone_triggers_public_secret_checks(origin):
    with pytest.raises(ValueError, match="JWT_SECRET_KEY"):
        validate_public_deployment_secrets(
            public_app_url="http://localhost:8080",
            cors_origins=f"http://localhost:8080,{origin}",
            jwt_secret_key="local-development-only-change-before-production",
            credential_encryption_key="c" * 48,
        )


@pytest.mark.parametrize("field", ["jwt_secret_key", "credential_encryption_key"])
@pytest.mark.parametrize("value", [
    "replace-with-at-least-32-random-bytes",
    "replace-with-a-separate-at-least-32-byte-random-secret",
    "replace-with-a-separate-long-random-secret",
    "change-this-in-production",
    "local-development-only-change-before-production",
    " " * 48,
    "too-short",
])
def test_public_deployment_rejects_placeholder_in_either_secret(field, value):
    secrets = {"jwt_secret_key": "j" * 48, "credential_encryption_key": "c" * 48}
    secrets[field] = value
    with pytest.raises(ValueError, match=field.upper()):
        validate_public_deployment_secrets(
            public_app_url="https://write.example.com", cors_origins="http://localhost:8080", **secrets,
        )


def test_public_deployment_rejects_reused_key_material():
    with pytest.raises(ValueError, match="CREDENTIAL_ENCRYPTION_KEY"):
        validate_public_deployment_secrets(
            public_app_url="https://write.example.com", cors_origins="https://write.example.com",
            jwt_secret_key="j" * 48, credential_encryption_key="j" * 48,
        )


def test_settings_startup_validation_does_not_print_secret_inputs():
    # Exercise the real Settings constructor used by API, workers and migrations.
    # Explicit synthetic values avoid reading the developer's private .env.
    with pytest.raises(ValidationError) as caught:
        Settings(
            _env_file=None,
            public_app_url="https://write.example.com", cors_origins="https://write.example.com",
            jwt_secret_key="replace-with-at-least-32-random-bytes", credential_encryption_key="c" * 48,
            model_gateway_cheap_url="https://provider.example/v1", model_gateway_cheap_key="sentinel-cheap",
            model_gateway_main_url="https://provider.example/v1", model_gateway_main_key="sentinel-private-key",
            model_gateway_premium_url="https://provider.example/v1", model_gateway_premium_key="sentinel-premium",
            s3_endpoint="https://storage.example", s3_access_key="sentinel-access", s3_secret_key="sentinel-secret",
        )
    message = str(caught.value)
    assert "JWT_SECRET_KEY" in message
    assert "sentinel" not in message
    assert "input_value" not in message
    assert "replace-with-at-least-32-random-bytes" not in message

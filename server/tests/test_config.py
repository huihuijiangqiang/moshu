import pytest

from config import validate_public_deployment_secrets


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

import base64
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import urlencode

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.x509.oid import NameOID

from providers.payments import (
    AlipayAdapter,
    PaymentAdapterRegistry,
    PaymentSignatureError,
    WeChatPayAdapter,
)


def private_pem(key):
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def public_pem(key):
    return key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def sign(key, message: bytes) -> str:
    raw = key.sign(message, padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(raw).decode("ascii")


def make_certificate(key):
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "WeChat Pay test platform")])
    return (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(days=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )


@pytest.fixture
def payment_keys(tmp_path):
    merchant = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    platform = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    merchant_path = tmp_path / "merchant.pem"
    merchant_path.write_bytes(private_pem(merchant))
    public_path = tmp_path / "alipay-public.pem"
    public_path.write_bytes(public_pem(platform))
    cert_dir = tmp_path / "wechat-certs"
    cert_dir.mkdir()
    certificate = make_certificate(platform)
    (cert_dir / "platform.pem").write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    return merchant, platform, merchant_path, public_path, cert_dir, certificate


def wechat_config(payment_keys):
    _, _, merchant_path, _, cert_dir, _ = payment_keys
    return SimpleNamespace(
        wechat_pay_private_key_path=str(merchant_path),
        wechat_pay_api_v3_key="0123456789abcdef0123456789abcdef",
        wechat_pay_platform_certificates_dir=str(cert_dir),
        wechat_pay_mch_id="1900000001",
        wechat_pay_serial_no="MERCHANTSERIAL",
        wechat_pay_app_id="wx-test-app",
        payment_notify_base_url="https://example.test/billing/webhooks",
        payment_request_timeout=1,
        wechat_pay_api_base_url="https://api.mch.weixin.qq.com",
    )


async def test_wechat_webhook_signature_and_aes_gcm_decryption(payment_keys):
    _, platform, _, _, _, certificate = payment_keys
    config = wechat_config(payment_keys)
    adapter = WeChatPayAdapter(config)
    data = {
        "appid": "wx-test-app",
        "mchid": "1900000001",
        "out_trade_no": "ord_wechat_test",
        "transaction_id": "4200000000001",
        "amount": {"total": 990, "currency": "CNY"},
    }
    nonce = "payment12345"
    associated = "transaction"
    ciphertext = AESGCM(config.wechat_pay_api_v3_key.encode()).encrypt(
        nonce.encode(), json.dumps(data).encode(), associated.encode()
    )
    body = json.dumps(
        {
            "id": "evt_wechat_1",
            "event_type": "TRANSACTION.SUCCESS",
            "resource_type": "encrypt-resource",
            "resource": {
                "nonce": nonce,
                "associated_data": associated,
                "ciphertext": base64.b64encode(ciphertext).decode(),
            },
        },
        separators=(",", ":"),
    ).encode()
    timestamp = str(int(datetime.now(UTC).timestamp()))
    response_nonce = "callback-nonce"
    signature = sign(
        platform,
        timestamp.encode() + b"\n" + response_nonce.encode() + b"\n" + body + b"\n",
    )
    headers = {
        "Wechatpay-Timestamp": timestamp,
        "Wechatpay-Nonce": response_nonce,
        "Wechatpay-Serial": format(certificate.serial_number, "X"),
        "Wechatpay-Signature": signature,
    }
    event = await adapter.verify_webhook(headers=headers, body=body)
    assert event.event_id == "evt_wechat_1"
    assert event.order_id == "ord_wechat_test"
    assert event.state == "paid"
    assert event.amount_minor == 990

    headers["Wechatpay-Signature"] = base64.b64encode(b"bad signature").decode()
    with pytest.raises(PaymentSignatureError):
        await adapter.verify_webhook(headers=headers, body=body)


async def test_alipay_response_and_notification_signatures(payment_keys):
    _, platform, merchant_path, public_path, _, _ = payment_keys
    result = {"code": "10000", "msg": "Success", "qr_code": "https://qr.alipay.test/order"}
    signed_content = json.dumps(result, separators=(",", ":"), ensure_ascii=False)
    envelope = (
        '{"alipay_trade_precreate_response":'
        + signed_content
        + ',"sign":'
        + json.dumps(sign(platform, signed_content.encode()))
        + "}"
    )

    def handler(request):
        assert request.url == httpx.URL("https://openapi.alipay.com/gateway.do")
        return httpx.Response(200, text=envelope, headers={"content-type": "application/json"})

    config = SimpleNamespace(
        alipay_private_key_path=str(merchant_path),
        alipay_alipay_public_key_path=str(public_path),
        alipay_app_id="2026000000000001",
        alipay_gateway_url="https://openapi.alipay.com/gateway.do",
        payment_notify_base_url="https://example.test/billing/webhooks",
        payment_request_timeout=1,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = AlipayAdapter(config, client)
        checkout = await adapter.create_checkout(
            order_id="ord_alipay_test",
            description="作者积分包",
            amount_minor=990,
            currency="CNY",
        )
        assert checkout.checkout_url == "https://qr.alipay.test/order"

        params = {
            "app_id": "2026000000000001",
            "notify_id": "notify_alipay_1",
            "notify_type": "trade_status_sync",
            "out_trade_no": "ord_alipay_test",
            "trade_no": "2026090800001",
            "trade_status": "TRADE_SUCCESS",
            "total_amount": "9.90",
            "sign_type": "RSA2",
        }
        params["sign"] = sign(platform, adapter._canonical(params).encode())
        event = await adapter.verify_webhook(headers={}, body=urlencode(params).encode())
        assert event.order_id == "ord_alipay_test"
        assert event.state == "paid"
        assert event.amount_minor == 990


def test_registry_reports_missing_and_invalid_configuration(payment_keys):
    _, _, merchant_path, public_path, cert_dir, _ = payment_keys
    config = SimpleNamespace(
        payment_notify_base_url=None,
        wechat_pay_app_id=None,
        wechat_pay_mch_id=None,
        wechat_pay_serial_no=None,
        wechat_pay_private_key_path=None,
        wechat_pay_api_v3_key=None,
        wechat_pay_platform_certificates_dir=None,
        alipay_app_id=None,
        alipay_private_key_path=None,
        alipay_alipay_public_key_path=None,
    )
    missing = PaymentAdapterRegistry(config).readiness("wechat")
    assert missing["state"] == "credentials_required"
    assert "WECHAT_PAY_APP_ID" in missing["missing"]

    config.payment_notify_base_url = "http://insecure.test/callback"
    config.wechat_pay_app_id = "wx-test"
    config.wechat_pay_mch_id = "mch-test"
    config.wechat_pay_serial_no = "serial"
    config.wechat_pay_private_key_path = str(merchant_path)
    config.wechat_pay_api_v3_key = "too-short"
    config.wechat_pay_platform_certificates_dir = str(cert_dir)
    invalid = PaymentAdapterRegistry(config).readiness("wechat")
    assert invalid["state"] == "invalid_configuration"
    assert set(invalid["invalid"]) == {"PAYMENT_NOTIFY_BASE_URL", "WECHAT_PAY_API_V3_KEY"}

    config.payment_notify_base_url = "https://example.test/callback"
    config.alipay_app_id = "app"
    config.alipay_private_key_path = str(merchant_path)
    config.alipay_alipay_public_key_path = str(public_path)
    assert PaymentAdapterRegistry(config).readiness("alipay")["state"] == "ready"

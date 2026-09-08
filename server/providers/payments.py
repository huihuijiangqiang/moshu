"""Payment-provider adapters for WeChat Pay API v3 and Alipay OpenAPI.

The billing service owns products, orders, and credit entitlements.  This
module only translates provider protocols into a small verified result model.
No provider payload is allowed to mutate local balances directly.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import parse_qsl, quote

import httpx
from cryptography import x509
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from config import Settings, settings

ProviderName = Literal["wechat", "alipay"]
ProviderState = Literal["pending", "paid", "cancelled", "failed", "refund_pending", "refunded", "partially_refunded"]


class PaymentProviderError(Exception):
    code = "PAYMENT_PROVIDER_ERROR"

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class PaymentProviderNotConfiguredError(PaymentProviderError):
    code = "PAYMENT_PROVIDER_NOT_CONFIGURED"


class PaymentSignatureError(PaymentProviderError):
    code = "PAYMENT_SIGNATURE_INVALID"


class PaymentResponseError(PaymentProviderError):
    code = "PAYMENT_PROVIDER_RESPONSE_INVALID"


@dataclass(frozen=True)
class CheckoutResult:
    checkout_url: str
    provider_checkout_id: str | None = None
    expires_at: datetime | None = None


@dataclass(frozen=True)
class ProviderOrderResult:
    state: ProviderState
    provider_order_id: str | None = None
    provider_refund_id: str | None = None
    amount_minor: int | None = None
    refunded_amount_minor: int = 0
    currency: str = "CNY"
    failure_code: str | None = None


@dataclass(frozen=True)
class VerifiedPaymentEvent:
    event_id: str
    order_id: str
    state: ProviderState
    provider_order_id: str | None = None
    provider_refund_id: str | None = None
    amount_minor: int | None = None
    refunded_amount_minor: int = 0
    currency: str = "CNY"
    event_type: str = "unknown"
    safe_payload: dict[str, Any] = field(default_factory=dict)


class PaymentAdapter(Protocol):
    name: ProviderName

    async def create_checkout(
        self, *, order_id: str, description: str, amount_minor: int, currency: str
    ) -> CheckoutResult: ...

    async def query_order(self, *, order_id: str) -> ProviderOrderResult: ...

    async def close_order(self, *, order_id: str) -> ProviderOrderResult: ...

    async def refund_order(
        self, *, order_id: str, provider_order_id: str | None, amount_minor: int, reason: str | None
    ) -> ProviderOrderResult: ...

    async def query_refund(
        self, *, order_id: str, provider_refund_id: str | None, amount_minor: int
    ) -> ProviderOrderResult: ...

    async def verify_webhook(self, *, headers: dict[str, str], body: bytes) -> VerifiedPaymentEvent: ...


def _read_required(path: str | None, label: str) -> bytes:
    if not path:
        raise PaymentProviderNotConfiguredError(f"{label} is not configured")
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise PaymentProviderNotConfiguredError(f"{label} cannot be read") from exc


def _valid_certificate(path: Path) -> bool:
    try:
        x509.load_pem_x509_certificate(path.read_bytes())
        return True
    except (OSError, ValueError):
        return False


def _valid_public_key(path: Path) -> bool:
    try:
        data = path.read_bytes()
        try:
            serialization.load_pem_public_key(data)
        except ValueError:
            x509.load_pem_x509_certificate(data)
        return True
    except (OSError, ValueError):
        return False


def _rsa_sign(private_key: Any, message: bytes) -> str:
    signature = private_key.sign(message, padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(signature).decode("ascii")


def _rsa_verify(public_key: Any, message: bytes, signature: str) -> None:
    try:
        public_key.verify(base64.b64decode(signature), message, padding.PKCS1v15(), hashes.SHA256())
    except Exception as exc:
        raise PaymentSignatureError("provider signature verification failed") from exc


def _yuan_to_minor(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int((Decimal(value) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError) as exc:
        raise PaymentResponseError("invalid provider amount") from exc


class WeChatPayAdapter:
    name: ProviderName = "wechat"

    def __init__(self, config: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.config = config
        self.client = client
        key_data = _read_required(config.wechat_pay_private_key_path, "WECHAT_PAY_PRIVATE_KEY_PATH")
        try:
            self.private_key = serialization.load_pem_private_key(key_data, password=None)
        except (ValueError, TypeError) as exc:
            raise PaymentProviderNotConfiguredError("WECHAT_PAY_PRIVATE_KEY_PATH is not a valid PEM key") from exc
        api_key = (config.wechat_pay_api_v3_key or "").encode("utf-8")
        if len(api_key) != 32:
            raise PaymentProviderNotConfiguredError("WECHAT_PAY_API_V3_KEY must contain exactly 32 bytes")
        self.api_v3_key = api_key
        cert_dir = Path(config.wechat_pay_platform_certificates_dir or "")
        if not cert_dir.is_dir():
            raise PaymentProviderNotConfiguredError("WECHAT_PAY_PLATFORM_CERTIFICATES_DIR cannot be read")
        self.platform_keys: dict[str, Any] = {}
        for path in cert_dir.glob("*.pem"):
            try:
                cert = x509.load_pem_x509_certificate(path.read_bytes())
                serial = format(cert.serial_number, "X")
                self.platform_keys[serial] = cert.public_key()
            except (OSError, ValueError):
                continue
        if not self.platform_keys:
            raise PaymentProviderNotConfiguredError("no WeChat platform certificate was loaded")

    def _notify_url(self) -> str:
        base = (self.config.payment_notify_base_url or "").rstrip("/")
        if not base:
            raise PaymentProviderNotConfiguredError("PAYMENT_NOTIFY_BASE_URL is not configured")
        return f"{base}/wechat"

    def _authorization(self, method: str, path_query: str, body: bytes) -> str:
        timestamp = str(int(datetime.now(UTC).timestamp()))
        nonce = secrets.token_hex(16)
        message = f"{method}\n{path_query}\n{timestamp}\n{nonce}\n".encode() + body + b"\n"
        signature = _rsa_sign(self.private_key, message)
        token = (
            f'mchid="{self.config.wechat_pay_mch_id}",nonce_str="{nonce}",'
            f'timestamp="{timestamp}",serial_no="{self.config.wechat_pay_serial_no}",signature="{signature}"'
        )
        return f"WECHATPAY2-SHA256-RSA2048 {token}"

    def _verify_message(self, headers: dict[str, str], body: bytes) -> None:
        normalized = {key.lower(): value for key, value in headers.items()}
        timestamp = normalized.get("wechatpay-timestamp")
        nonce = normalized.get("wechatpay-nonce")
        signature = normalized.get("wechatpay-signature")
        serial = (normalized.get("wechatpay-serial") or "").upper()
        if not timestamp or not nonce or not signature or not serial:
            raise PaymentSignatureError("required WeChat signature headers are missing")
        try:
            signed_at = datetime.fromtimestamp(int(timestamp), UTC)
        except (ValueError, OSError) as exc:
            raise PaymentSignatureError("invalid WeChat timestamp") from exc
        if abs((datetime.now(UTC) - signed_at).total_seconds()) > 300:
            raise PaymentSignatureError("stale WeChat signature")
        public_key = self.platform_keys.get(serial)
        if public_key is None:
            raise PaymentSignatureError("unknown WeChat platform certificate serial")
        _rsa_verify(public_key, timestamp.encode() + b"\n" + nonce.encode() + b"\n" + body + b"\n", signature)

    async def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode() if payload is not None else b""
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": self._authorization(method, path, body),
            "User-Agent": "moshu-billing/1.0",
        }
        owns_client = self.client is None
        client = self.client or httpx.AsyncClient(timeout=self.config.payment_request_timeout)
        try:
            response = await client.request(
                method, self.config.wechat_pay_api_base_url.rstrip("/") + path, content=body, headers=headers
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise PaymentProviderError("WeChat Pay request failed", retryable=True) from exc
        finally:
            if owns_client:
                await client.aclose()
        if response.status_code not in {200, 204}:
            try:
                code = response.json().get("code", "HTTP_ERROR")
            except ValueError:
                code = "HTTP_ERROR"
            raise PaymentProviderError(f"WeChat Pay rejected request: {code}", retryable=response.status_code >= 500)
        self._verify_message(dict(response.headers), response.content)
        return response.json() if response.content else {}

    async def create_checkout(
        self, *, order_id: str, description: str, amount_minor: int, currency: str
    ) -> CheckoutResult:
        expires_at = datetime.now(UTC) + timedelta(minutes=30)
        result = await self._request(
            "POST",
            "/v3/pay/transactions/native",
            {
                "appid": self.config.wechat_pay_app_id,
                "mchid": self.config.wechat_pay_mch_id,
                "description": description[:127],
                "out_trade_no": order_id,
                "notify_url": self._notify_url(),
                "time_expire": expires_at.isoformat(timespec="seconds"),
                "amount": {"total": amount_minor, "currency": currency},
            },
        )
        code_url = result.get("code_url")
        if not isinstance(code_url, str) or not code_url:
            raise PaymentResponseError("WeChat Pay did not return code_url")
        return CheckoutResult(code_url, code_url, expires_at)

    async def query_order(self, *, order_id: str) -> ProviderOrderResult:
        result = await self._request(
            "GET",
            f"/v3/pay/transactions/out-trade-no/{quote(order_id)}?mchid={quote(str(self.config.wechat_pay_mch_id))}",
        )
        state = {"SUCCESS": "paid", "CLOSED": "cancelled", "PAYERROR": "failed", "REFUND": "refunded"}.get(
            result.get("trade_state"), "pending"
        )
        amount = result.get("amount") or {}
        return ProviderOrderResult(
            state=state,
            provider_order_id=result.get("transaction_id"),
            amount_minor=amount.get("total"),
            currency=amount.get("currency", "CNY"),
            failure_code=result.get("trade_state_desc"),
        )

    async def close_order(self, *, order_id: str) -> ProviderOrderResult:
        await self._request(
            "POST",
            f"/v3/pay/transactions/out-trade-no/{quote(order_id)}/close",
            {"mchid": self.config.wechat_pay_mch_id},
        )
        return ProviderOrderResult(state="cancelled")

    async def refund_order(
        self, *, order_id: str, provider_order_id: str | None, amount_minor: int, reason: str | None
    ) -> ProviderOrderResult:
        result = await self._request(
            "POST",
            "/v3/refund/domestic/refunds",
            {
                "out_trade_no": order_id,
                "out_refund_no": f"refund_{order_id}",
                "reason": (reason or "user requested refund")[:80],
                "notify_url": self._notify_url(),
                "amount": {"refund": amount_minor, "total": amount_minor, "currency": "CNY"},
            },
        )
        state = {"SUCCESS": "refunded", "CLOSED": "failed", "ABNORMAL": "failed"}.get(
            result.get("status"), "refund_pending"
        )
        amount = result.get("amount") or {}
        return ProviderOrderResult(
            state=state,
            provider_refund_id=result.get("refund_id"),
            refunded_amount_minor=amount.get("refund", 0),
            amount_minor=amount.get("total"),
            failure_code=result.get("status"),
        )

    async def query_refund(
        self, *, order_id: str, provider_refund_id: str | None, amount_minor: int
    ) -> ProviderOrderResult:
        result = await self._request("GET", f"/v3/refund/domestic/refunds/{quote(f'refund_{order_id}')}")
        state = {"SUCCESS": "refunded", "CLOSED": "failed", "ABNORMAL": "failed"}.get(
            result.get("status"), "refund_pending"
        )
        amount = result.get("amount") or {}
        return ProviderOrderResult(
            state=state,
            provider_refund_id=result.get("refund_id"),
            refunded_amount_minor=amount.get("refund", 0),
            amount_minor=amount.get("total"),
            failure_code=result.get("status"),
        )

    async def verify_webhook(self, *, headers: dict[str, str], body: bytes) -> VerifiedPaymentEvent:
        self._verify_message(headers, body)
        try:
            envelope = json.loads(body)
            resource = envelope["resource"]
            plaintext = AESGCM(self.api_v3_key).decrypt(
                resource["nonce"].encode(),
                base64.b64decode(resource["ciphertext"]),
                resource.get("associated_data", "").encode(),
            )
            data = json.loads(plaintext)
        except (KeyError, ValueError, TypeError, json.JSONDecodeError, InvalidTag) as exc:
            raise PaymentResponseError("invalid encrypted WeChat webhook") from exc
        if data.get("mchid") != self.config.wechat_pay_mch_id:
            raise PaymentSignatureError("WeChat notification merchant does not match configuration")
        if data.get("appid") and data.get("appid") != self.config.wechat_pay_app_id:
            raise PaymentSignatureError("WeChat notification app does not match configuration")
        event_type = str(envelope.get("event_type", "unknown"))
        is_refund = event_type.startswith("REFUND.")
        if is_refund:
            state = {"REFUND.SUCCESS": "refunded", "REFUND.CLOSED": "failed", "REFUND.ABNORMAL": "failed"}.get(
                event_type, "refund_pending"
            )
            amount = data.get("amount") or {}
            order_id = data.get("out_trade_no")
            refunded = amount.get("refund", 0)
            provider_order_id = data.get("transaction_id")
            provider_refund_id = data.get("refund_id")
        else:
            state = {
                "TRANSACTION.SUCCESS": "paid",
                "TRANSACTION.CLOSED": "cancelled",
                "TRANSACTION.PAYERROR": "failed",
            }.get(event_type, "pending")
            amount = data.get("amount") or {}
            order_id = data.get("out_trade_no")
            refunded = 0
            provider_order_id = data.get("transaction_id")
            provider_refund_id = None
        if not isinstance(order_id, str) or not order_id:
            raise PaymentResponseError("WeChat webhook has no out_trade_no")
        return VerifiedPaymentEvent(
            event_id=str(envelope.get("id") or hashlib.sha256(body).hexdigest()),
            order_id=order_id,
            state=state,
            provider_order_id=provider_order_id,
            provider_refund_id=provider_refund_id,
            amount_minor=amount.get("total"),
            refunded_amount_minor=int(refunded or 0),
            currency=amount.get("currency", "CNY"),
            event_type=event_type,
            safe_payload={"event_type": event_type, "resource_type": envelope.get("resource_type")},
        )


class AlipayAdapter:
    name: ProviderName = "alipay"

    def __init__(self, config: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.config = config
        self.client = client
        try:
            self.private_key = serialization.load_pem_private_key(
                _read_required(config.alipay_private_key_path, "ALIPAY_PRIVATE_KEY_PATH"), password=None
            )
        except (ValueError, TypeError) as exc:
            raise PaymentProviderNotConfiguredError("ALIPAY_PRIVATE_KEY_PATH is not a valid PEM key") from exc
        public_data = _read_required(config.alipay_alipay_public_key_path, "ALIPAY_ALIPAY_PUBLIC_KEY_PATH")
        try:
            self.alipay_public_key = serialization.load_pem_public_key(public_data)
        except ValueError:
            try:
                self.alipay_public_key = x509.load_pem_x509_certificate(public_data).public_key()
            except ValueError as exc:
                raise PaymentProviderNotConfiguredError(
                    "ALIPAY_ALIPAY_PUBLIC_KEY_PATH is not a valid PEM key or certificate"
                ) from exc

    def _notify_url(self) -> str:
        base = (self.config.payment_notify_base_url or "").rstrip("/")
        if not base:
            raise PaymentProviderNotConfiguredError("PAYMENT_NOTIFY_BASE_URL is not configured")
        return f"{base}/alipay"

    @staticmethod
    def _canonical(params: dict[str, Any]) -> str:
        return "&".join(
            f"{key}={params[key]}"
            for key in sorted(params)
            if key not in {"sign", "sign_type"} and params[key] not in {None, ""}
        )

    async def _call(self, method: str, biz_content: dict[str, Any], *, notify: bool = False) -> dict:
        params: dict[str, Any] = {
            "app_id": self.config.alipay_app_id,
            "method": method,
            "format": "JSON",
            "charset": "utf-8",
            "sign_type": "RSA2",
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "version": "1.0",
            "biz_content": json.dumps(biz_content, separators=(",", ":"), ensure_ascii=False),
        }
        if notify:
            params["notify_url"] = self._notify_url()
        params["sign"] = _rsa_sign(self.private_key, self._canonical(params).encode("utf-8"))
        owns_client = self.client is None
        client = self.client or httpx.AsyncClient(timeout=self.config.payment_request_timeout)
        try:
            response = await client.post(self.config.alipay_gateway_url, data=params)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise PaymentProviderError("Alipay request failed", retryable=True) from exc
        finally:
            if owns_client:
                await client.aclose()
        if response.status_code != 200:
            raise PaymentProviderError("Alipay HTTP request failed", retryable=response.status_code >= 500)
        response_key = method.replace(".", "_") + "_response"
        try:
            parsed = response.json()
            result = parsed[response_key]
            signature = parsed["sign"]
            raw_text = response.text
            marker = json.dumps(response_key)
            start = raw_text.index(marker) + len(marker)
            whitespace = raw_text[start:].lstrip()
            if not whitespace.startswith(":"):
                raise ValueError("response key is not followed by a colon")
            start += len(raw_text[start:]) - len(whitespace) + 1
            decoder = json.JSONDecoder()
            _, consumed = decoder.raw_decode(raw_text[start:])
            signed_content = raw_text[start : start + consumed]
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise PaymentResponseError("invalid Alipay response envelope") from exc
        _rsa_verify(self.alipay_public_key, signed_content.encode("utf-8"), signature)
        if result.get("code") != "10000":
            sub_code = result.get("sub_code") or result.get("code") or "UNKNOWN"
            raise PaymentProviderError(
                f"Alipay rejected request: {sub_code}", retryable=sub_code in {"ACQ.SYSTEM_ERROR", "SYSTEM_ERROR"}
            )
        return result

    async def create_checkout(
        self, *, order_id: str, description: str, amount_minor: int, currency: str
    ) -> CheckoutResult:
        result = await self._call(
            "alipay.trade.precreate",
            {"out_trade_no": order_id, "total_amount": f"{amount_minor / 100:.2f}", "subject": description[:256]},
            notify=True,
        )
        qr_code = result.get("qr_code")
        if not isinstance(qr_code, str) or not qr_code:
            raise PaymentResponseError("Alipay did not return qr_code")
        return CheckoutResult(qr_code, qr_code, datetime.now(UTC) + timedelta(minutes=30))

    async def query_order(self, *, order_id: str) -> ProviderOrderResult:
        result = await self._call("alipay.trade.query", {"out_trade_no": order_id})
        state = {"TRADE_SUCCESS": "paid", "TRADE_FINISHED": "paid", "TRADE_CLOSED": "cancelled"}.get(
            result.get("trade_status"), "pending"
        )
        total = _yuan_to_minor(result.get("total_amount"))
        refunded = _yuan_to_minor(result.get("refund_amount")) or 0
        if total and refunded >= total:
            state = "refunded"
        elif refunded:
            state = "partially_refunded"
        return ProviderOrderResult(
            state=state, provider_order_id=result.get("trade_no"), amount_minor=total, refunded_amount_minor=refunded
        )

    async def close_order(self, *, order_id: str) -> ProviderOrderResult:
        await self._call("alipay.trade.close", {"out_trade_no": order_id})
        return ProviderOrderResult(state="cancelled")

    async def refund_order(
        self, *, order_id: str, provider_order_id: str | None, amount_minor: int, reason: str | None
    ) -> ProviderOrderResult:
        out_request_no = f"refund_{order_id}"
        result = await self._call(
            "alipay.trade.refund",
            {
                "out_trade_no": order_id,
                "refund_amount": f"{amount_minor / 100:.2f}",
                "refund_reason": (reason or "user requested refund")[:256],
                "out_request_no": out_request_no,
            },
        )
        state: ProviderState = "refunded" if result.get("fund_change") == "Y" else "refund_pending"
        return ProviderOrderResult(
            state=state,
            provider_order_id=result.get("trade_no"),
            provider_refund_id=out_request_no,
            amount_minor=amount_minor,
            refunded_amount_minor=_yuan_to_minor(result.get("refund_fee"))
            or (amount_minor if state == "refunded" else 0),
        )

    async def query_refund(
        self, *, order_id: str, provider_refund_id: str | None, amount_minor: int
    ) -> ProviderOrderResult:
        out_request_no = provider_refund_id or f"refund_{order_id}"
        result = await self._call(
            "alipay.trade.fastpay.refund.query", {"out_trade_no": order_id, "out_request_no": out_request_no}
        )
        refunded = _yuan_to_minor(result.get("refund_amount")) or 0
        state: ProviderState = (
            "refunded" if refunded >= amount_minor else ("partially_refunded" if refunded else "refund_pending")
        )
        return ProviderOrderResult(
            state=state,
            provider_order_id=result.get("trade_no"),
            provider_refund_id=out_request_no,
            amount_minor=amount_minor,
            refunded_amount_minor=refunded,
        )

    async def verify_webhook(self, *, headers: dict[str, str], body: bytes) -> VerifiedPaymentEvent:
        try:
            params = dict(parse_qsl(body.decode("utf-8"), keep_blank_values=True))
            signature = params["sign"]
        except (UnicodeDecodeError, KeyError) as exc:
            raise PaymentSignatureError("invalid Alipay notification") from exc
        _rsa_verify(self.alipay_public_key, self._canonical(params).encode("utf-8"), signature)
        if params.get("app_id") != self.config.alipay_app_id:
            raise PaymentSignatureError("Alipay notification app does not match configuration")
        order_id = params.get("out_trade_no")
        if not order_id:
            raise PaymentResponseError("Alipay notification has no out_trade_no")
        trade_status = params.get("trade_status")
        state = {"TRADE_SUCCESS": "paid", "TRADE_FINISHED": "paid", "TRADE_CLOSED": "cancelled"}.get(
            trade_status, "pending"
        )
        total = _yuan_to_minor(params.get("total_amount"))
        refunded = _yuan_to_minor(params.get("refund_fee")) or 0
        if refunded and total and refunded >= total:
            state = "refunded"
        elif refunded:
            state = "partially_refunded"
        event_type = trade_status or ("REFUND" if refunded else "UNKNOWN")
        return VerifiedPaymentEvent(
            event_id=params.get("notify_id") or hashlib.sha256(body).hexdigest(),
            order_id=order_id,
            state=state,
            provider_order_id=params.get("trade_no"),
            amount_minor=total,
            refunded_amount_minor=refunded,
            event_type=event_type,
            safe_payload={"trade_status": trade_status, "notify_type": params.get("notify_type")},
        )


class PaymentAdapterRegistry:
    def __init__(self, config: Settings = settings) -> None:
        self.config = config

    def readiness(self, provider: str) -> dict[str, Any]:
        required = {
            "wechat": {
                "WECHAT_PAY_APP_ID": self.config.wechat_pay_app_id,
                "WECHAT_PAY_MCH_ID": self.config.wechat_pay_mch_id,
                "WECHAT_PAY_SERIAL_NO": self.config.wechat_pay_serial_no,
                "WECHAT_PAY_PRIVATE_KEY_PATH": self.config.wechat_pay_private_key_path,
                "WECHAT_PAY_API_V3_KEY": self.config.wechat_pay_api_v3_key,
                "WECHAT_PAY_PLATFORM_CERTIFICATES_DIR": self.config.wechat_pay_platform_certificates_dir,
                "PAYMENT_NOTIFY_BASE_URL": self.config.payment_notify_base_url,
            },
            "alipay": {
                "ALIPAY_APP_ID": self.config.alipay_app_id,
                "ALIPAY_PRIVATE_KEY_PATH": self.config.alipay_private_key_path,
                "ALIPAY_ALIPAY_PUBLIC_KEY_PATH": self.config.alipay_alipay_public_key_path,
                "PAYMENT_NOTIFY_BASE_URL": self.config.payment_notify_base_url,
            },
        }
        values = required.get(provider)
        if values is None:
            return {"configured": False, "state": "unsupported", "missing": [], "invalid": []}
        missing = [key for key, value in values.items() if not value]
        invalid: list[str] = []
        if not missing:
            if not str(self.config.payment_notify_base_url).startswith("https://"):
                invalid.append("PAYMENT_NOTIFY_BASE_URL")
            private_path = (
                self.config.wechat_pay_private_key_path if provider == "wechat" else self.config.alipay_private_key_path
            )
            if not Path(str(private_path)).is_file():
                invalid.append("WECHAT_PAY_PRIVATE_KEY_PATH" if provider == "wechat" else "ALIPAY_PRIVATE_KEY_PATH")
            else:
                try:
                    serialization.load_pem_private_key(Path(str(private_path)).read_bytes(), password=None)
                except (OSError, ValueError, TypeError):
                    invalid.append("WECHAT_PAY_PRIVATE_KEY_PATH" if provider == "wechat" else "ALIPAY_PRIVATE_KEY_PATH")
            if provider == "wechat":
                if len(str(self.config.wechat_pay_api_v3_key).encode("utf-8")) != 32:
                    invalid.append("WECHAT_PAY_API_V3_KEY")
                cert_dir = Path(str(self.config.wechat_pay_platform_certificates_dir))
                if not cert_dir.is_dir() or not any(_valid_certificate(path) for path in cert_dir.glob("*.pem")):
                    invalid.append("WECHAT_PAY_PLATFORM_CERTIFICATES_DIR")
            else:
                public_path = Path(str(self.config.alipay_alipay_public_key_path))
                if not public_path.is_file() or not _valid_public_key(public_path):
                    invalid.append("ALIPAY_ALIPAY_PUBLIC_KEY_PATH")
        configured = not missing and not invalid
        state = "ready" if configured else ("credentials_required" if missing else "invalid_configuration")
        return {
            "configured": configured,
            "state": state,
            "missing": missing,
            "invalid": invalid,
        }

    def get(self, provider: str) -> PaymentAdapter:
        readiness = self.readiness(provider)
        if not readiness["configured"]:
            raise PaymentProviderNotConfiguredError(f"{provider} payment provider is not configured")
        if provider == "wechat":
            return WeChatPayAdapter(self.config)
        if provider == "alipay":
            return AlipayAdapter(self.config)
        raise PaymentProviderNotConfiguredError(f"unsupported payment provider: {provider}")


def get_payment_registry() -> PaymentAdapterRegistry:
    return PaymentAdapterRegistry(settings)


__all__ = [
    "AlipayAdapter",
    "CheckoutResult",
    "PaymentAdapter",
    "PaymentAdapterRegistry",
    "PaymentProviderError",
    "PaymentProviderNotConfiguredError",
    "PaymentResponseError",
    "PaymentSignatureError",
    "ProviderOrderResult",
    "VerifiedPaymentEvent",
    "WeChatPayAdapter",
    "get_payment_registry",
]

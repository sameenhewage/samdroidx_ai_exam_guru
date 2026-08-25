import asyncio
import base64
import binascii
import json
import math
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from secrets import compare_digest
from typing import Any, Protocol, TypeGuard, cast
from urllib.parse import urlsplit
from uuid import UUID, uuid5

import jwt
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from jwt import PyJWK
from jwt.exceptions import PyJWKClientConnectionError, PyJWTError

from exam_guru_api.auth.domain import AdminRole, Principal
from exam_guru_api.auth.ports import (
    AuthenticationError,
    AuthenticationFailureCode,
    DenyAllIdentityProvider,
    IdentityProvider,
)
from exam_guru_api.core.config import Settings

MAX_ACCESS_TOKEN_LENGTH = 8_192
MAX_OIDC_HEADER_BYTES = 1_024
MAX_OIDC_CLAIMS_BYTES = 4_096
MAX_OIDC_KID_LENGTH = 256
MAX_OIDC_SUBJECT_LENGTH = 512
MAX_OIDC_ROLE_LENGTH = 256
MAX_OIDC_ROLE_COUNT = 32
OIDC_SUBJECT_NAMESPACE = UUID("91c7f06d-8ef7-5db3-92d4-1c903f2d77b7")
_FIXED_OIDC_ALGORITHMS = frozenset({"RS256", "ES256"})


@dataclass(frozen=True, slots=True)
class _Identity:
    token: str
    principal: Principal


class DeterministicIdentityProvider:
    def __init__(self, identities: tuple[_Identity, ...]) -> None:
        self._identities = identities

    async def authenticate(self, access_token: str) -> Principal:
        for identity in self._identities:
            if compare_digest(access_token, identity.token):
                return identity.principal
        raise AuthenticationError(AuthenticationFailureCode.INVALID)


class TokenVerifier(Protocol):
    def verify(self, access_token: str) -> Mapping[str, object]: ...


class JWKClient(Protocol):
    def get_signing_key_from_jwt(self, token: str) -> PyJWK: ...


class JWKSetClient(Protocol):
    def get_signing_keys(self, refresh: bool = False) -> list[PyJWK]: ...


class TokenVerificationError(ValueError):
    def __init__(self) -> None:
        super().__init__(AuthenticationFailureCode.INVALID.value)


class TokenVerificationUnavailableError(RuntimeError):
    def __init__(self) -> None:
        super().__init__(AuthenticationFailureCode.UNAVAILABLE.value)


class BoundedJWKClient:
    def __init__(
        self,
        delegate: JWKSetClient,
        *,
        cache_seconds: int,
        max_cached_keys: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if cache_seconds < 1 or max_cached_keys < 1:
            raise ValueError("JWKS cache bounds must be positive")
        self._delegate = delegate
        self._cache_seconds = cache_seconds
        self._max_cached_keys = max_cached_keys
        self._clock = clock
        self._keys: dict[str, PyJWK] = {}
        self._expires_at = 0.0
        self._lock = threading.Lock()

    def get_signing_key_from_jwt(self, token: str) -> PyJWK:
        header = _parse_compact_token(token)
        kid = header.get("kid")
        if not isinstance(kid, str):
            raise TokenVerificationError
        now = self._clock()
        if not math.isfinite(now):
            raise TokenVerificationUnavailableError
        if now < self._expires_at:
            return self._get_cached_key(kid)
        with self._lock:
            now = self._clock()
            if not math.isfinite(now):
                raise TokenVerificationUnavailableError
            if now < self._expires_at:
                return self._get_cached_key(kid)
            try:
                signing_keys = self._delegate.get_signing_keys(refresh=False)
            except (PyJWKClientConnectionError, TimeoutError, OSError):
                raise TokenVerificationUnavailableError from None
            except Exception:
                raise TokenVerificationUnavailableError from None
            if not 1 <= len(signing_keys) <= self._max_cached_keys:
                raise TokenVerificationUnavailableError
            bounded_keys: dict[str, PyJWK] = {}
            for signing_key in signing_keys:
                key_id = signing_key.key_id
                if (
                    not isinstance(key_id, str)
                    or not key_id
                    or len(key_id) > MAX_OIDC_KID_LENGTH
                    or key_id in bounded_keys
                ):
                    raise TokenVerificationUnavailableError
                bounded_keys[key_id] = signing_key
            self._keys = bounded_keys
            self._expires_at = now + self._cache_seconds
            return self._get_cached_key(kid)

    def _get_cached_key(self, kid: str) -> PyJWK:
        try:
            return self._keys[kid]
        except KeyError:
            raise TokenVerificationError from None


class PyJWTTokenVerifier:
    def __init__(self, settings: Settings, *, jwks_client: JWKClient | None = None) -> None:
        self._issuer = _required_string(settings.oidc_issuer)
        self._audience = _required_string(settings.oidc_audience)
        self._clock_skew_seconds = _required_int(settings.oidc_clock_skew_seconds)
        self._algorithms = tuple(settings.oidc_algorithms)
        if not self._algorithms or not set(self._algorithms) <= _FIXED_OIDC_ALGORITHMS:
            raise ValueError("OIDC algorithms must be a fixed asymmetric subset")
        if jwks_client is None:
            cache_seconds = _required_int(settings.oidc_jwks_cache_seconds)
            max_cached_keys = _required_int(settings.oidc_jwks_max_cached_keys)
            timeout_seconds = _required_float(settings.oidc_jwks_timeout_seconds)
            delegate = cast(
                JWKSetClient,
                jwt.PyJWKClient(
                    _required_string(settings.oidc_jwks_url),
                    cache_keys=False,
                    max_cached_keys=max_cached_keys,
                    cache_jwk_set=True,
                    lifespan=cache_seconds,
                    timeout=cast(int, timeout_seconds),
                ),
            )
            jwks_client = BoundedJWKClient(
                delegate,
                cache_seconds=cache_seconds,
                max_cached_keys=max_cached_keys,
            )
        self._jwks_client = jwks_client

    def verify(self, access_token: str) -> Mapping[str, object]:
        try:
            header = _parse_compact_token(access_token)
            algorithm = header.get("alg")
            kid = header.get("kid")
            if not isinstance(algorithm, str) or algorithm not in self._algorithms:
                raise TokenVerificationError
            if (
                not isinstance(kid, str)
                or not kid
                or len(kid) > MAX_OIDC_KID_LENGTH
                or kid != kid.strip()
                or not kid.isprintable()
                or any(character.isspace() for character in kid)
            ):
                raise TokenVerificationError
            if {"crit", "b64", "jku", "jwk", "x5u"} & header.keys():
                raise TokenVerificationError
            signing_key = self._jwks_client.get_signing_key_from_jwt(access_token)
            key_algorithm = getattr(signing_key, "algorithm_name", None)
            key = getattr(signing_key, "key", None)
            if key_algorithm != algorithm or not _is_valid_signing_key(algorithm, key):
                raise TokenVerificationError
            decoded = jwt.decode(
                access_token,
                cast(Any, key),
                algorithms=list(self._algorithms),
                audience=self._audience,
                issuer=self._issuer,
                leeway=self._clock_skew_seconds,
                options={
                    "require": ["sub", "iat", "exp", "iss", "aud"],
                    "strict_aud": True,
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_iat": True,
                    "verify_nbf": True,
                    "verify_iss": True,
                    "verify_aud": True,
                    "verify_sub": True,
                },
            )
            if not isinstance(decoded, dict):
                raise TokenVerificationError
            return cast(dict[str, object], decoded)
        except (TokenVerificationError, TokenVerificationUnavailableError):
            raise
        except (PyJWKClientConnectionError, TimeoutError, OSError):
            raise TokenVerificationUnavailableError from None
        except (
            PyJWTError,
            ValueError,
            TypeError,
            UnicodeError,
            OverflowError,
            RecursionError,
            binascii.Error,
        ):
            raise TokenVerificationError from None
        except Exception:
            raise TokenVerificationUnavailableError from None


class OIDCIdentityProvider:
    def __init__(
        self,
        settings: Settings,
        *,
        verifier: TokenVerifier | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._issuer = _required_string(settings.oidc_issuer)
        self._audience = _required_string(settings.oidc_audience)
        self._role_claim_name = _required_string(settings.oidc_role_claim_name)
        self._admin_role = _required_string(settings.oidc_admin_role)
        self._reviewer_role = _required_string(settings.oidc_reviewer_role)
        self._max_token_age_seconds = _required_int(settings.oidc_max_token_age_seconds)
        self._clock_skew_seconds = _required_int(settings.oidc_clock_skew_seconds)
        self._verifier = verifier if verifier is not None else PyJWTTokenVerifier(settings)
        self._clock = clock

    async def authenticate(self, access_token: str) -> Principal:
        try:
            claims = await asyncio.to_thread(self._verifier.verify, access_token)
        except TokenVerificationError:
            raise AuthenticationError(AuthenticationFailureCode.INVALID) from None
        except TokenVerificationUnavailableError:
            raise AuthenticationError(AuthenticationFailureCode.UNAVAILABLE) from None
        except Exception:
            raise AuthenticationError(AuthenticationFailureCode.UNAVAILABLE) from None
        try:
            return self._principal_from_claims(claims)
        except (KeyError, TypeError, ValueError, OverflowError):
            raise AuthenticationError(AuthenticationFailureCode.INVALID) from None

    def _principal_from_claims(self, claims: Mapping[str, object]) -> Principal:
        if not isinstance(claims, Mapping):
            raise TypeError("invalid claims")
        issuer = claims["iss"]
        audience = claims["aud"]
        subject = claims["sub"]
        issued_at = claims["iat"]
        expires_at = claims["exp"]
        not_before = claims.get("nbf")
        if not isinstance(issuer, str) or not compare_digest(issuer, self._issuer):
            raise ValueError("invalid issuer")
        if not isinstance(audience, str) or not compare_digest(audience, self._audience):
            raise ValueError("invalid audience")
        if (
            not isinstance(subject, str)
            or not subject
            or len(subject) > MAX_OIDC_SUBJECT_LENGTH
            or subject != subject.strip()
            or not subject.isprintable()
        ):
            raise ValueError("invalid subject")
        if not _is_numeric_date(issued_at) or not _is_numeric_date(expires_at):
            raise ValueError("invalid token time")
        if not_before is not None and not _is_numeric_date(not_before):
            raise ValueError("invalid not-before time")
        now = self._clock()
        if not math.isfinite(now):
            raise ValueError("invalid clock")
        if expires_at <= issued_at:
            raise ValueError("invalid token lifetime")
        if expires_at - issued_at > self._max_token_age_seconds:
            raise ValueError("token lifetime exceeds maximum")
        if now - issued_at > self._max_token_age_seconds:
            raise ValueError("token age exceeds maximum")
        if issued_at > now + self._clock_skew_seconds:
            raise ValueError("token issued in future")
        if expires_at <= now - self._clock_skew_seconds:
            raise ValueError("token expired")
        if not_before is not None and not_before > now + self._clock_skew_seconds:
            raise ValueError("token not active")
        roles = _map_roles(
            claims[self._role_claim_name],
            admin_role=self._admin_role,
            reviewer_role=self._reviewer_role,
        )
        return Principal(
            subject_id=oidc_subject_id(self._issuer, subject),
            roles=roles,
        )


def oidc_subject_id(issuer: str, subject: str) -> UUID:
    if (
        not subject
        or len(subject) > MAX_OIDC_SUBJECT_LENGTH
        or subject != subject.strip()
        or not subject.isprintable()
    ):
        raise ValueError("OIDC subject must be bounded control-free text")
    canonical_name = json.dumps(
        [_canonical_issuer(issuer), subject],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return uuid5(OIDC_SUBJECT_NAMESPACE, canonical_name)


def _canonical_issuer(issuer: str) -> str:
    parsed = urlsplit(issuer)
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        raise ValueError("OIDC issuer must be an absolute HTTP URL")
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    port = parsed.port
    default_port = 443 if parsed.scheme == "https" else 80
    authority = host if port in {None, default_port} else f"{host}:{port}"
    return f"{parsed.scheme.lower()}://{authority}{parsed.path}"


def _parse_compact_token(access_token: str) -> dict[str, object]:
    if (
        not isinstance(access_token, str)
        or not access_token
        or len(access_token) > MAX_ACCESS_TOKEN_LENGTH
        or not access_token.isascii()
    ):
        raise TokenVerificationError
    segments = access_token.split(".")
    if len(segments) != 3 or any(not segment for segment in segments):
        raise TokenVerificationError
    header = _decode_json_segment(segments[0], MAX_OIDC_HEADER_BYTES)
    claims = _decode_json_segment(segments[1], MAX_OIDC_CLAIMS_BYTES)
    if not isinstance(header, dict) or not isinstance(claims, dict):
        raise TokenVerificationError
    return cast(dict[str, object], header)


def _decode_json_segment(segment: str, maximum_bytes: int) -> object:
    if len(segment) > ((maximum_bytes + 2) // 3) * 4:
        raise TokenVerificationError
    padding = "=" * (-len(segment) % 4)
    decoded = base64.b64decode(segment + padding, altchars=b"-_", validate=True)
    if len(decoded) > maximum_bytes:
        raise TokenVerificationError
    return json.loads(
        decoded,
        object_pairs_hook=_unique_json_object,
        parse_constant=_reject_json_constant,
    )


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise TokenVerificationError
        value[key] = item
    return value


def _reject_json_constant(value: str) -> object:
    del value
    raise TokenVerificationError


def _map_roles(
    raw_roles: object,
    *,
    admin_role: str,
    reviewer_role: str,
) -> frozenset[AdminRole]:
    if isinstance(raw_roles, str):
        provider_roles = [raw_roles]
    elif isinstance(raw_roles, list) and 1 <= len(raw_roles) <= MAX_OIDC_ROLE_COUNT:
        provider_roles = raw_roles
    else:
        raise ValueError("invalid role claim")
    if any(
        not isinstance(role, str)
        or not role
        or len(role) > MAX_OIDC_ROLE_LENGTH
        or role != role.strip()
        or not role.isprintable()
        or any(character.isspace() for character in role)
        for role in provider_roles
    ):
        raise ValueError("invalid provider role")
    if len(provider_roles) != len(set(provider_roles)):
        raise ValueError("duplicate provider role")
    mapped: set[AdminRole] = set()
    if admin_role in provider_roles:
        mapped.add(AdminRole.ADMIN)
    if reviewer_role in provider_roles:
        mapped.add(AdminRole.REVIEWER)
    if not mapped:
        raise ValueError("no allowed provider role")
    return frozenset(mapped)


def _is_numeric_date(value: object) -> TypeGuard[int]:
    return type(value) is int


def _is_valid_signing_key(algorithm: str, key: object) -> bool:
    if algorithm == "RS256":
        return isinstance(key, rsa.RSAPublicKey) and key.key_size >= 2_048
    if algorithm == "ES256":
        return isinstance(key, ec.EllipticCurvePublicKey) and isinstance(
            key.curve,
            ec.SECP256R1,
        )
    return False


def _required_string(value: str | None) -> str:
    if value is None:
        raise ValueError("OIDC configuration is incomplete")
    return value


def _required_int(value: int | None) -> int:
    if value is None:
        raise ValueError("OIDC configuration is incomplete")
    return value


def _required_float(value: float | None) -> float:
    if value is None:
        raise ValueError("OIDC configuration is incomplete")
    return value


def build_identity_provider(settings: Settings) -> IdentityProvider:
    if settings.environment == "production" and settings.identity_provider != "oidc":
        raise ValueError("production identity provider must be OIDC")
    if settings.identity_provider == "deny":
        return DenyAllIdentityProvider()
    if settings.identity_provider == "oidc":
        return OIDCIdentityProvider(settings)
    if settings.identity_provider != "deterministic":
        raise ValueError("unsupported identity provider")
    identities: list[_Identity] = []
    if settings.deterministic_admin_token is not None:
        identities.append(
            _Identity(
                token=settings.deterministic_admin_token.get_secret_value(),
                principal=Principal(
                    subject_id=settings.deterministic_admin_subject_id,
                    roles=frozenset({AdminRole.ADMIN}),
                ),
            )
        )
    if settings.deterministic_reviewer_token is not None:
        identities.append(
            _Identity(
                token=settings.deterministic_reviewer_token.get_secret_value(),
                principal=Principal(
                    subject_id=settings.deterministic_reviewer_subject_id,
                    roles=frozenset({AdminRole.REVIEWER}),
                ),
            )
        )
    if not identities:
        return DenyAllIdentityProvider()
    return DeterministicIdentityProvider(tuple(identities))

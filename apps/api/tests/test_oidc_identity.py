import asyncio
import base64
import json
import threading
import time
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import Any, cast
from uuid import UUID

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from jwt import PyJWK
from jwt.algorithms import ECAlgorithm, RSAAlgorithm
from jwt.exceptions import PyJWKClientConnectionError, PyJWKClientError
from jwt.warnings import InsecureKeyLengthWarning

from exam_guru_api.auth import adapters as auth_adapters
from exam_guru_api.auth.adapters import (
    MAX_ACCESS_TOKEN_LENGTH,
    MAX_OIDC_CLAIMS_BYTES,
    MAX_OIDC_HEADER_BYTES,
    MAX_OIDC_KID_LENGTH,
    MAX_OIDC_ROLE_COUNT,
    MAX_OIDC_ROLE_LENGTH,
    MAX_OIDC_SUBJECT_LENGTH,
    OIDC_SUBJECT_NAMESPACE,
    BoundedJWKClient,
    OIDCIdentityProvider,
    PyJWTTokenVerifier,
    TokenVerificationError,
    TokenVerificationUnavailableError,
    TokenVerifier,
    build_identity_provider,
    oidc_subject_id,
)
from exam_guru_api.auth.domain import AdminRole
from exam_guru_api.auth.ports import (
    AuthenticationError,
    AuthenticationFailureCode,
    DenyAllIdentityProvider,
)
from exam_guru_api.core.config import Settings

ISSUER = "https://identity.internal.example/realms/exam-guru"
AUDIENCE = "exam-guru-api"
ADMIN_ROLE = "exam-guru-admin"
REVIEWER_ROLE = "exam-guru-reviewer"
ROLE_CLAIM = "realm_roles"
RSA_KID = "rsa-key-1"
EC_KID = "ec-key-1"


def oidc_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "test",
        "identity_provider": "oidc",
        "oidc_issuer": ISSUER,
        "oidc_audience": AUDIENCE,
        "oidc_jwks_url": f"{ISSUER}/protocol/openid-connect/certs",
        "oidc_role_claim_name": ROLE_CLAIM,
        "oidc_admin_role": ADMIN_ROLE,
        "oidc_reviewer_role": REVIEWER_ROLE,
        "oidc_max_token_age_seconds": 600,
        "oidc_clock_skew_seconds": 5,
        "oidc_jwks_timeout_seconds": 2.0,
        "oidc_jwks_cache_seconds": 300,
        "oidc_jwks_max_cached_keys": 8,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


@pytest.fixture(scope="module")
def rsa_private_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65_537, key_size=2_048)


@pytest.fixture(scope="module")
def other_rsa_private_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65_537, key_size=2_048)


@pytest.fixture(scope="module")
def ec_private_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def pyjwk_for_rsa(key: rsa.RSAPrivateKey, kid: str = RSA_KID) -> PyJWK:
    raw = RSAAlgorithm.to_jwk(key.public_key())
    value = cast(dict[str, Any], json.loads(raw))
    value.update({"alg": "RS256", "kid": kid, "use": "sig"})
    return PyJWK.from_dict(value)


def pyjwk_for_ec(key: ec.EllipticCurvePrivateKey, kid: str = EC_KID) -> PyJWK:
    raw = ECAlgorithm.to_jwk(key.public_key())
    value = cast(dict[str, Any], json.loads(raw))
    value.update({"alg": "ES256", "kid": kid, "use": "sig"})
    return PyJWK.from_dict(value)


class StaticJWKClient:
    def __init__(self, keys: Mapping[str, PyJWK]) -> None:
        self.keys = dict(keys)
        self.calls = 0

    def get_signing_key_from_jwt(self, token: str) -> PyJWK:
        self.calls += 1
        kid = jwt.get_unverified_header(token).get("kid")
        if not isinstance(kid, str) or kid not in self.keys:
            raise PyJWKClientError("unsafe unknown key detail")
        return self.keys[kid]


class FailingJWKClient:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def get_signing_key_from_jwt(self, token: str) -> PyJWK:
        del token
        raise self.error


def claims(now: int, **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "provider|user/arbitrary@example.internal",
        "iat": now - 30,
        "exp": now + 300,
        ROLE_CLAIM: [REVIEWER_ROLE],
    }
    values.update(overrides)
    return values


def encode_token(
    values: dict[str, object],
    key: rsa.RSAPrivateKey | ec.EllipticCurvePrivateKey | str,
    *,
    algorithm: str = "RS256",
    kid: str | None = RSA_KID,
    extra_headers: Mapping[str, object] | None = None,
) -> str:
    headers: dict[str, object] = dict(extra_headers or {})
    if kid is not None:
        headers["kid"] = kid
    return jwt.encode(values, key, algorithm=algorithm, headers=headers)


def encode_unchecked_claims(
    values: dict[str, object],
    key: rsa.RSAPrivateKey | ec.EllipticCurvePrivateKey,
    *,
    algorithm: str = "RS256",
    kid: str = RSA_KID,
) -> str:
    payload = json.dumps(values, separators=(",", ":")).encode()
    return jwt.api_jws.PyJWS().encode(
        payload,
        key,
        algorithm=algorithm,
        headers={"kid": kid},
    )


def provider_for(
    settings: Settings,
    jwks_client: StaticJWKClient | FailingJWKClient,
    *,
    now: int,
) -> OIDCIdentityProvider:
    verifier = PyJWTTokenVerifier(settings, jwks_client=jwks_client)
    return OIDCIdentityProvider(settings, verifier=verifier, clock=lambda: float(now))


def assert_authentication_failure(
    provider: OIDCIdentityProvider,
    token: str,
    code: AuthenticationFailureCode = AuthenticationFailureCode.INVALID,
) -> AuthenticationError:
    with pytest.raises(AuthenticationError) as raised:
        asyncio.run(provider.authenticate(token))
    assert raised.value.code is code
    assert str(raised.value) == code.value
    assert raised.value.__cause__ is None
    if isinstance(token, str) and token:
        assert token not in str(raised.value)
    return raised.value


def compact_json_token(header: object, payload: object) -> str:
    def segment(value: object) -> str:
        encoded = json.dumps(value, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(encoded).rstrip(b"=").decode()

    return f"{segment(header)}.{segment(payload)}.c2lnbmF0dXJl"


def raw_json_token(header: str, payload: str) -> str:
    def segment(value: str) -> str:
        return base64.urlsafe_b64encode(value.encode()).rstrip(b"=").decode()

    return f"{segment(header)}.{segment(payload)}.c2lnbmF0dXJl"


@pytest.mark.parametrize(
    ("role_value", "expected_roles"),
    [
        (ADMIN_ROLE, frozenset({AdminRole.ADMIN})),
        ([REVIEWER_ROLE], frozenset({AdminRole.REVIEWER})),
        (
            [REVIEWER_ROLE, "unrelated-provider-role", ADMIN_ROLE],
            frozenset({AdminRole.ADMIN, AdminRole.REVIEWER}),
        ),
    ],
)
def test_oidc_identity_accepts_signed_rsa_tokens_and_maps_only_exact_roles(
    rsa_private_key: rsa.RSAPrivateKey,
    role_value: str | list[str],
    expected_roles: frozenset[AdminRole],
) -> None:
    now = int(time.time())
    settings = oidc_settings()
    client = StaticJWKClient({RSA_KID: pyjwk_for_rsa(rsa_private_key)})
    token = encode_token(claims(now, **{ROLE_CLAIM: role_value}), rsa_private_key)

    principal = asyncio.run(provider_for(settings, client, now=now).authenticate(token))

    assert principal.roles == expected_roles
    assert principal.subject_id == oidc_subject_id(
        ISSUER,
        "provider|user/arbitrary@example.internal",
    )
    assert client.calls == 1


def test_oidc_identity_accepts_es256_when_enabled(
    ec_private_key: ec.EllipticCurvePrivateKey,
) -> None:
    now = int(time.time())
    settings = oidc_settings(oidc_algorithms=("ES256",))
    client = StaticJWKClient({EC_KID: pyjwk_for_ec(ec_private_key)})
    token = encode_token(
        claims(now, **{ROLE_CLAIM: [ADMIN_ROLE, REVIEWER_ROLE]}),
        ec_private_key,
        algorithm="ES256",
        kid=EC_KID,
    )

    principal = asyncio.run(provider_for(settings, client, now=now).authenticate(token))

    assert principal.roles == frozenset({AdminRole.ADMIN, AdminRole.REVIEWER})


def test_subject_uuid_is_stable_canonical_and_not_based_on_truncation() -> None:
    prefix = "subject-" + "x" * (MAX_OIDC_SUBJECT_LENGTH - 10)
    first = oidc_subject_id("HTTPS://Identity.Internal.Example:443/issuer", prefix + "a")
    second = oidc_subject_id("https://identity.internal.example/issuer", prefix + "b")

    assert isinstance(OIDC_SUBJECT_NAMESPACE, UUID)
    assert first == oidc_subject_id(
        "https://identity.internal.example/issuer",
        prefix + "a",
    )
    assert first != second
    assert first.version == 5


@pytest.mark.parametrize(
    "token_factory",
    [
        lambda now, key: encode_token(claims(now, iss="https://wrong.example/issuer"), key),
        lambda now, key: encode_token(claims(now, aud="another-api"), key),
        lambda now, key: encode_token(claims(now, aud=[AUDIENCE]), key),
        lambda now, key: encode_token(claims(now, exp=now - 10), key),
        lambda now, key: encode_token(claims(now, nbf=now + 10), key),
        lambda now, key: encode_token(claims(now, iat=now + 10, exp=now + 100), key),
        lambda now, key: encode_token(claims(now, iat=now - 601, exp=now + 1), key),
        lambda now, key: encode_token(claims(now, iat=now - 10, exp=now + 591), key),
        lambda now, key: encode_token(claims(now, iat=now, exp=now), key),
    ],
    ids=[
        "issuer",
        "audience",
        "audience-list-not-exact",
        "expired",
        "nbf",
        "future-iat",
        "old-iat",
        "exp-minus-iat",
        "nonpositive-lifetime",
    ],
)
def test_oidc_identity_rejects_invalid_registered_claims(
    rsa_private_key: rsa.RSAPrivateKey,
    token_factory: Any,
) -> None:
    now = int(time.time())
    client = StaticJWKClient({RSA_KID: pyjwk_for_rsa(rsa_private_key)})
    token = cast(str, token_factory(now, rsa_private_key))

    assert_authentication_failure(provider_for(oidc_settings(), client, now=now), token)


@pytest.mark.parametrize("missing_claim", ["sub", "iat", "exp", "iss", "aud"])
def test_oidc_identity_rejects_missing_required_claims(
    rsa_private_key: rsa.RSAPrivateKey,
    missing_claim: str,
) -> None:
    now = int(time.time())
    values = claims(now)
    del values[missing_claim]
    token = encode_token(values, rsa_private_key)
    client = StaticJWKClient({RSA_KID: pyjwk_for_rsa(rsa_private_key)})

    assert_authentication_failure(provider_for(oidc_settings(), client, now=now), token)


@pytest.mark.parametrize(
    "overrides",
    [
        {"sub": 123},
        {"sub": ""},
        {"sub": " subject"},
        {"sub": "subject\x00suffix"},
        {"sub": "s" * (MAX_OIDC_SUBJECT_LENGTH + 1)},
        {"iat": "100"},
        {"iat": True},
        {"iat": 1.5},
        {"exp": "200"},
        {"nbf": "100"},
        {"iss": [ISSUER]},
        {"aud": 123},
    ],
)
def test_oidc_identity_rejects_malformed_claim_types_and_bounds(
    rsa_private_key: rsa.RSAPrivateKey,
    overrides: dict[str, object],
) -> None:
    now = int(time.time())
    token = encode_unchecked_claims(claims(now, **overrides), rsa_private_key)
    client = StaticJWKClient({RSA_KID: pyjwk_for_rsa(rsa_private_key)})

    assert_authentication_failure(provider_for(oidc_settings(), client, now=now), token)


@pytest.mark.parametrize(
    "role_value",
    [
        "ADMIN",
        [],
        [REVIEWER_ROLE, REVIEWER_ROLE],
        [REVIEWER_ROLE, 5],
        [REVIEWER_ROLE, [ADMIN_ROLE]],
        [""],
        ["r" * (MAX_OIDC_ROLE_LENGTH + 1)],
        ["reviewer\x00spoof"],
        [" reviewer"],
        ["reviewer role"],
        [f"unknown-{index}" for index in range(MAX_OIDC_ROLE_COUNT + 1)],
        5,
        {"role": ADMIN_ROLE},
    ],
)
def test_oidc_role_claim_rejects_spoofed_duplicate_malformed_and_unbounded_values(
    rsa_private_key: rsa.RSAPrivateKey,
    role_value: object,
) -> None:
    now = int(time.time())
    token = encode_token(claims(now, **{ROLE_CLAIM: role_value}), rsa_private_key)
    client = StaticJWKClient({RSA_KID: pyjwk_for_rsa(rsa_private_key)})

    assert_authentication_failure(provider_for(oidc_settings(), client, now=now), token)


def test_oidc_identity_rejects_missing_role_claim(
    rsa_private_key: rsa.RSAPrivateKey,
) -> None:
    now = int(time.time())
    values = claims(now)
    del values[ROLE_CLAIM]
    token = encode_token(values, rsa_private_key)
    client = StaticJWKClient({RSA_KID: pyjwk_for_rsa(rsa_private_key)})

    assert_authentication_failure(provider_for(oidc_settings(), client, now=now), token)


def test_oidc_identity_rejects_wrong_signature_and_unknown_key(
    rsa_private_key: rsa.RSAPrivateKey,
    other_rsa_private_key: rsa.RSAPrivateKey,
) -> None:
    now = int(time.time())
    client = StaticJWKClient({RSA_KID: pyjwk_for_rsa(rsa_private_key)})
    wrong_signature = encode_token(claims(now), other_rsa_private_key)
    unknown_key = encode_token(claims(now), rsa_private_key, kid="unknown-key")
    provider = provider_for(oidc_settings(), client, now=now)

    assert_authentication_failure(provider, wrong_signature)
    assert_authentication_failure(provider, unknown_key)


@pytest.mark.parametrize(
    "algorithm",
    ["none", "HS256", "RS512"],
)
def test_oidc_identity_rejects_none_hmac_and_unapproved_algorithms_before_jwks(
    algorithm: str,
    rsa_private_key: rsa.RSAPrivateKey,
) -> None:
    now = int(time.time())
    key = "symmetric-confusion-key-material-over-32-bytes" if algorithm == "HS256" else ""
    if algorithm == "RS512":
        key = cast(str, rsa_private_key)
    token = encode_token(claims(now), key, algorithm=algorithm)
    client = StaticJWKClient({RSA_KID: pyjwk_for_rsa(rsa_private_key)})

    assert_authentication_failure(provider_for(oidc_settings(), client, now=now), token)
    assert client.calls == 0


def test_oidc_identity_rejects_weak_rsa_and_wrong_curve_keys() -> None:
    now = int(time.time())
    weak_rsa = rsa.generate_private_key(
        public_exponent=65_537,
        key_size=1_024,  # noqa: S505 - deliberately rejected weak fixture
    )
    with pytest.warns(InsecureKeyLengthWarning):
        weak_token = encode_token(claims(now), weak_rsa)
    weak_client = StaticJWKClient({RSA_KID: pyjwk_for_rsa(weak_rsa)})
    assert_authentication_failure(provider_for(oidc_settings(), weak_client, now=now), weak_token)

    wrong_curve = ec.generate_private_key(ec.SECP384R1())
    wrong_curve_jwk = pyjwk_for_ec(wrong_curve)
    valid_curve_signer = ec.generate_private_key(ec.SECP256R1())
    wrong_curve_token = encode_token(
        claims(now),
        valid_curve_signer,
        algorithm="ES256",
        kid=EC_KID,
    )
    wrong_curve_client = StaticJWKClient({EC_KID: wrong_curve_jwk})
    assert_authentication_failure(
        provider_for(oidc_settings(oidc_algorithms=("ES256",)), wrong_curve_client, now=now),
        wrong_curve_token,
    )


def test_oidc_identity_rejects_algorithm_outside_configured_subset(
    ec_private_key: ec.EllipticCurvePrivateKey,
) -> None:
    now = int(time.time())
    token = encode_token(claims(now), ec_private_key, algorithm="ES256", kid=EC_KID)
    client = StaticJWKClient({EC_KID: pyjwk_for_ec(ec_private_key)})

    assert_authentication_failure(
        provider_for(oidc_settings(oidc_algorithms=("RS256",)), client, now=now),
        token,
    )
    assert client.calls == 0


@pytest.mark.parametrize(
    "token",
    [
        cast(str, b"not-text"),
        "",
        "not-a-jwt",
        "a.b.c.d",
        "@@@.e30.signature",
        "e30..signature",
        "é.e30.signature",
        compact_json_token([], {}),
        compact_json_token({"alg": 5, "kid": RSA_KID}, {}),
        compact_json_token({"alg": "RS256", "kid": RSA_KID}, []),
        raw_json_token(
            json.dumps({"alg": "RS256", "kid": RSA_KID}),
            "[" * 2_000 + "0" + "]" * 2_000,
        ),
        "x" * (MAX_ACCESS_TOKEN_LENGTH + 1),
    ],
)
def test_oidc_identity_rejects_malformed_and_oversized_compact_tokens(token: str) -> None:
    now = int(time.time())
    client = StaticJWKClient({})

    assert_authentication_failure(provider_for(oidc_settings(), client, now=now), token)
    assert client.calls == 0


def test_oidc_identity_rejects_duplicate_json_members_and_nonfinite_constants_before_jwks() -> None:
    now = int(time.time())
    valid_payload = json.dumps(claims(now), separators=(",", ":"))
    duplicate_header = raw_json_token(
        f'{{"alg":"RS256","alg":"RS256","kid":"{RSA_KID}"}}',
        valid_payload,
    )
    duplicate_claim = raw_json_token(
        json.dumps({"alg": "RS256", "kid": RSA_KID}, separators=(",", ":")),
        valid_payload[:-1] + f',"{ROLE_CLAIM}":["{ADMIN_ROLE}"]}}',
    )
    nonfinite_claim = raw_json_token(
        json.dumps({"alg": "RS256", "kid": RSA_KID}, separators=(",", ":")),
        valid_payload[:-1] + ',"malformed":NaN}',
    )
    client = StaticJWKClient({})
    provider = provider_for(oidc_settings(), client, now=now)

    for token in (duplicate_header, duplicate_claim, nonfinite_claim):
        assert_authentication_failure(provider, token)
    assert client.calls == 0


def test_oidc_identity_rejects_missing_malformed_and_oversized_kid(
    rsa_private_key: rsa.RSAPrivateKey,
) -> None:
    now = int(time.time())
    tokens = (
        encode_token(claims(now), rsa_private_key, kid=None),
        encode_token(claims(now), rsa_private_key, kid=""),
        encode_token(claims(now), rsa_private_key, kid="key value"),
        encode_token(claims(now), rsa_private_key, kid="key\x00suffix"),
        encode_token(claims(now), rsa_private_key, kid="k" * (MAX_OIDC_KID_LENGTH + 1)),
    )
    client = StaticJWKClient({RSA_KID: pyjwk_for_rsa(rsa_private_key)})
    provider = provider_for(oidc_settings(), client, now=now)

    for token in tokens:
        assert_authentication_failure(provider, token)
    assert client.calls == 0


def test_oidc_identity_rejects_oversized_decoded_header_and_claims(
    rsa_private_key: rsa.RSAPrivateKey,
) -> None:
    now = int(time.time())
    oversized_header = encode_token(
        claims(now),
        rsa_private_key,
        extra_headers={"padding": "h" * (MAX_OIDC_HEADER_BYTES + 1)},
    )
    oversized_claims = encode_token(
        claims(now, padding="c" * (MAX_OIDC_CLAIMS_BYTES + 1)),
        rsa_private_key,
    )
    client = StaticJWKClient({RSA_KID: pyjwk_for_rsa(rsa_private_key)})
    provider = provider_for(oidc_settings(), client, now=now)

    assert len(oversized_header) <= MAX_ACCESS_TOKEN_LENGTH
    assert len(oversized_claims) <= MAX_ACCESS_TOKEN_LENGTH
    assert_authentication_failure(provider, oversized_header)
    assert_authentication_failure(provider, oversized_claims)
    assert client.calls == 0


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("unsafe timeout https://identity.internal.example/jwks"),
        OSError("unsafe network provider detail"),
        PyJWKClientConnectionError("unsafe connection provider detail"),
        RuntimeError("unsafe unexpected provider detail"),
    ],
)
def test_jwks_network_and_provider_failures_are_unavailable_and_sanitized(
    rsa_private_key: rsa.RSAPrivateKey,
    error: Exception,
) -> None:
    now = int(time.time())
    token = encode_token(claims(now), rsa_private_key)
    provider = provider_for(oidc_settings(), FailingJWKClient(error), now=now)

    raised = assert_authentication_failure(
        provider,
        token,
        AuthenticationFailureCode.UNAVAILABLE,
    )
    assert "unsafe" not in str(raised)
    assert ISSUER not in str(raised)


class ThreadRecordingVerifier:
    def __init__(self, returned_claims: Mapping[str, object], parties: int = 1) -> None:
        self.returned_claims = returned_claims
        self.thread_ids: list[int] = []
        self.barrier = threading.Barrier(parties) if parties > 1 else None

    def verify(self, access_token: str) -> Mapping[str, object]:
        del access_token
        self.thread_ids.append(threading.get_ident())
        if self.barrier is not None:
            self.barrier.wait(timeout=5)
        return self.returned_claims


def test_oidc_authentication_offloads_injected_synchronous_verifier_to_thread() -> None:
    now = int(time.time())
    verifier = ThreadRecordingVerifier(claims(now))
    provider = OIDCIdentityProvider(
        oidc_settings(),
        verifier=cast(TokenVerifier, verifier),
        clock=lambda: float(now),
    )
    caller_thread = threading.get_ident()

    principal = asyncio.run(provider.authenticate("opaque-injected-test-token"))

    assert principal.roles == frozenset({AdminRole.REVIEWER})
    assert verifier.thread_ids
    assert verifier.thread_ids[0] != caller_thread


def test_oidc_authentication_runs_concurrent_verification_without_blocking_event_loop() -> None:
    now = int(time.time())
    verifier = ThreadRecordingVerifier(claims(now), parties=2)
    provider = OIDCIdentityProvider(
        oidc_settings(),
        verifier=cast(TokenVerifier, verifier),
        clock=lambda: float(now),
    )

    async def authenticate_concurrently() -> tuple[UUID, UUID]:
        first, second = await asyncio.gather(
            provider.authenticate("first-opaque-token"),
            provider.authenticate("second-opaque-token"),
        )
        return first.subject_id, second.subject_id

    subject_ids = asyncio.run(authenticate_concurrently())

    assert subject_ids[0] == subject_ids[1]
    assert len(verifier.thread_ids) == 2
    assert len(set(verifier.thread_ids)) == 2


class RaisingVerifier:
    def verify(self, access_token: str) -> Mapping[str, object]:
        raise RuntimeError(f"unsafe verifier detail: {access_token}")


def test_unexpected_injected_verifier_failure_is_unavailable_without_token_leakage() -> None:
    now = int(time.time())
    token = "provider-secret-access-token"
    provider = OIDCIdentityProvider(
        oidc_settings(),
        verifier=cast(TokenVerifier, RaisingVerifier()),
        clock=lambda: float(now),
    )

    assert_authentication_failure(provider, token, AuthenticationFailureCode.UNAVAILABLE)


def test_pyjwt_verifier_builds_bounded_jwks_cache_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class CapturingPyJWKClient:
        def __init__(self, uri: str, **kwargs: object) -> None:
            captured["uri"] = uri
            captured.update(kwargs)

    monkeypatch.setattr(jwt, "PyJWKClient", CapturingPyJWKClient)
    settings = oidc_settings(
        oidc_jwks_timeout_seconds=1.5,
        oidc_jwks_cache_seconds=101,
        oidc_jwks_max_cached_keys=7,
    )

    PyJWTTokenVerifier(settings)

    assert captured == {
        "uri": f"{ISSUER}/protocol/openid-connect/certs",
        "cache_keys": False,
        "max_cached_keys": 7,
        "cache_jwk_set": True,
        "lifespan": 101,
        "timeout": 1.5,
    }


@pytest.mark.parametrize(
    "protected_header",
    [
        {"crit": ["provider-policy"], "provider-policy": "require-mfa"},
        {"b64": True},
        {"jku": "https://attacker.example/jwks"},
        {"jwk": {"kty": "oct", "k": "attacker-controlled"}},
        {"x5u": "https://attacker.example/certificate"},
    ],
)
def test_oidc_identity_rejects_unsupported_key_and_critical_headers_before_jwks(
    rsa_private_key: rsa.RSAPrivateKey,
    protected_header: dict[str, object],
) -> None:
    now = int(time.time())
    token = (
        compact_json_token(
            {"alg": "RS256", "kid": RSA_KID, **protected_header},
            claims(now),
        )
        if "b64" in protected_header
        else encode_token(
            claims(now),
            rsa_private_key,
            extra_headers=protected_header,
        )
    )
    client = StaticJWKClient({RSA_KID: pyjwk_for_rsa(rsa_private_key)})

    assert_authentication_failure(provider_for(oidc_settings(), client, now=now), token)
    assert client.calls == 0


def test_oidc_identity_rejects_jwk_algorithm_mismatch_before_signature_verification(
    rsa_private_key: rsa.RSAPrivateKey,
    ec_private_key: ec.EllipticCurvePrivateKey,
) -> None:
    now = int(time.time())
    token = encode_token(claims(now), rsa_private_key)
    client = StaticJWKClient({RSA_KID: pyjwk_for_ec(ec_private_key, kid=RSA_KID)})

    assert_authentication_failure(provider_for(oidc_settings(), client, now=now), token)


class FakeJWKSetClient:
    def __init__(self, keys: list[PyJWK], *, delay_seconds: float = 0) -> None:
        self.keys = keys
        self.delay_seconds = delay_seconds
        self.calls: list[bool] = []
        self.lock = threading.Lock()

    def get_signing_keys(self, refresh: bool = False) -> list[PyJWK]:
        with self.lock:
            self.calls.append(refresh)
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        return self.keys


def test_bounded_jwk_client_honors_cache_expiry_and_negative_caches_unknown_kids(
    rsa_private_key: rsa.RSAPrivateKey,
) -> None:
    now = [100.0]
    delegate = FakeJWKSetClient([pyjwk_for_rsa(rsa_private_key)])
    client = BoundedJWKClient(
        delegate,
        cache_seconds=10,
        max_cached_keys=2,
        clock=lambda: now[0],
    )
    valid_token = compact_json_token({"alg": "RS256", "kid": RSA_KID}, {})
    unknown_token = compact_json_token({"alg": "RS256", "kid": "random-attacker-kid"}, {})

    assert client.get_signing_key_from_jwt(valid_token).key_id == RSA_KID
    assert client.get_signing_key_from_jwt(valid_token).key_id == RSA_KID
    with pytest.raises(ValueError, match="invalid_access_token"):
        client.get_signing_key_from_jwt(unknown_token)
    assert delegate.calls == [False]

    now[0] = 109.999
    assert client.get_signing_key_from_jwt(valid_token).key_id == RSA_KID
    assert delegate.calls == [False]

    now[0] = 110.0
    assert client.get_signing_key_from_jwt(valid_token).key_id == RSA_KID
    assert delegate.calls == [False, False]


def test_bounded_jwk_client_serializes_cache_fill_under_concurrency(
    rsa_private_key: rsa.RSAPrivateKey,
) -> None:
    delegate = FakeJWKSetClient([pyjwk_for_rsa(rsa_private_key)], delay_seconds=0.05)
    client = BoundedJWKClient(
        delegate,
        cache_seconds=30,
        max_cached_keys=2,
        clock=lambda: 100.0,
    )
    token = compact_json_token({"alg": "RS256", "kid": RSA_KID}, {})

    with ThreadPoolExecutor(max_workers=4) as executor:
        keys = list(executor.map(client.get_signing_key_from_jwt, [token] * 4))

    assert [key.key_id for key in keys] == [RSA_KID] * 4
    assert delegate.calls == [False]


@pytest.mark.parametrize(
    ("cache_seconds", "max_cached_keys"),
    [(0, 1), (1, 0)],
)
def test_bounded_jwk_client_rejects_nonpositive_cache_bounds(
    cache_seconds: int,
    max_cached_keys: int,
) -> None:
    with pytest.raises(ValueError, match="cache bounds"):
        BoundedJWKClient(
            FakeJWKSetClient([]),
            cache_seconds=cache_seconds,
            max_cached_keys=max_cached_keys,
        )


def test_bounded_jwk_client_rejects_malformed_kid_and_nonfinite_clocks() -> None:
    malformed_kid_token = compact_json_token({"alg": "RS256", "kid": 5}, {})
    client = BoundedJWKClient(
        FakeJWKSetClient([]),
        cache_seconds=10,
        max_cached_keys=2,
    )
    with pytest.raises(TokenVerificationError):
        client.get_signing_key_from_jwt(malformed_kid_token)

    token = compact_json_token({"alg": "RS256", "kid": RSA_KID}, {})
    nonfinite = BoundedJWKClient(
        FakeJWKSetClient([]),
        cache_seconds=10,
        max_cached_keys=2,
        clock=lambda: float("nan"),
    )
    with pytest.raises(TokenVerificationUnavailableError):
        nonfinite.get_signing_key_from_jwt(token)

    clock_values = iter((100.0, float("inf")))
    nonfinite_after_lock = BoundedJWKClient(
        FakeJWKSetClient([]),
        cache_seconds=10,
        max_cached_keys=2,
        clock=lambda: next(clock_values),
    )
    with pytest.raises(TokenVerificationUnavailableError):
        nonfinite_after_lock.get_signing_key_from_jwt(token)


class RaisingJWKSetClient:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def get_signing_keys(self, refresh: bool = False) -> list[PyJWK]:
        del refresh
        raise self.error


@pytest.mark.parametrize(
    "error",
    [
        PyJWKClientConnectionError("unsafe URL detail"),
        RuntimeError("unsafe provider detail"),
    ],
)
def test_bounded_jwk_client_normalizes_delegate_failures(error: Exception) -> None:
    client = BoundedJWKClient(
        RaisingJWKSetClient(error),
        cache_seconds=10,
        max_cached_keys=2,
        clock=lambda: 100.0,
    )
    token = compact_json_token({"alg": "RS256", "kid": RSA_KID}, {})

    with pytest.raises(TokenVerificationUnavailableError) as raised:
        client.get_signing_key_from_jwt(token)
    assert str(raised.value) == "identity_provider_unavailable"
    assert raised.value.__cause__ is None


def test_bounded_jwk_client_rejects_unbounded_and_malformed_key_sets(
    rsa_private_key: rsa.RSAPrivateKey,
) -> None:
    valid = pyjwk_for_rsa(rsa_private_key)
    invalid_ids = (
        pyjwk_for_rsa(rsa_private_key, kid=""),
        pyjwk_for_rsa(rsa_private_key, kid="k" * (MAX_OIDC_KID_LENGTH + 1)),
        pyjwk_for_rsa(rsa_private_key, kid=cast(str, 5)),
    )
    key_sets = (
        ([], 2),
        ([valid, pyjwk_for_rsa(rsa_private_key, kid="second")], 1),
        ([valid, valid], 2),
        *(([invalid], 2) for invalid in invalid_ids),
    )
    token = compact_json_token({"alg": "RS256", "kid": RSA_KID}, {})

    for keys, maximum in key_sets:
        client = BoundedJWKClient(
            FakeJWKSetClient(keys),
            cache_seconds=10,
            max_cached_keys=maximum,
            clock=lambda: 100.0,
        )
        with pytest.raises(TokenVerificationUnavailableError):
            client.get_signing_key_from_jwt(token)


@pytest.mark.parametrize("algorithms", [(), ("HS256",)])
def test_pyjwt_verifier_defensively_rejects_invalid_constructed_algorithm_sets(
    algorithms: tuple[str, ...],
) -> None:
    settings = oidc_settings().model_copy(update={"oidc_algorithms": algorithms})
    with pytest.raises(ValueError, match="fixed asymmetric subset"):
        PyJWTTokenVerifier(settings, jwks_client=StaticJWKClient({}))


def test_pyjwt_verifier_normalizes_invalid_decode_shape_and_unexpected_failure(
    monkeypatch: pytest.MonkeyPatch,
    rsa_private_key: rsa.RSAPrivateKey,
) -> None:
    now = int(time.time())
    token = encode_token(claims(now), rsa_private_key)
    verifier = PyJWTTokenVerifier(
        oidc_settings(),
        jwks_client=StaticJWKClient({RSA_KID: pyjwk_for_rsa(rsa_private_key)}),
    )
    monkeypatch.setattr(jwt, "decode", lambda *args, **kwargs: [])
    with pytest.raises(TokenVerificationError):
        verifier.verify(token)

    def overflow_decode(*args: object, **kwargs: object) -> dict[str, object]:
        del args, kwargs
        raise OverflowError("unsafe malformed claim detail")

    monkeypatch.setattr(jwt, "decode", overflow_decode)
    with pytest.raises(TokenVerificationError) as invalid:
        verifier.verify(token)
    assert str(invalid.value) == "invalid_access_token"

    def fail_decode(*args: object, **kwargs: object) -> dict[str, object]:
        del args, kwargs
        raise RuntimeError("unsafe unexpected library detail")

    monkeypatch.setattr(jwt, "decode", fail_decode)
    with pytest.raises(TokenVerificationUnavailableError) as raised:
        verifier.verify(token)
    assert str(raised.value) == "identity_provider_unavailable"


class ObjectClaimsVerifier:
    def __init__(self, returned_claims: object) -> None:
        self.returned_claims = returned_claims

    def verify(self, access_token: str) -> Mapping[str, object]:
        del access_token
        return cast(Mapping[str, object], self.returned_claims)


@pytest.mark.parametrize(
    ("claim_overrides", "clock"),
    [
        ({"iss": 5}, 1_000.0),
        ({"iss": "https://wrong.example/issuer"}, 1_000.0),
        ({"aud": 5}, 1_000.0),
        ({"aud": "wrong-api"}, 1_000.0),
        ({}, float("nan")),
        ({"iat": 300, "exp": 850}, 1_000.0),
        ({"iat": 1_010, "exp": 1_100}, 1_000.0),
        ({"iat": 900, "exp": 994}, 1_000.0),
        ({"nbf": 1_006}, 1_000.0),
    ],
)
def test_injected_verifier_claims_still_pass_all_provider_invariants(
    claim_overrides: dict[str, object],
    clock: float,
) -> None:
    values = claims(1_000, **claim_overrides)
    provider = OIDCIdentityProvider(
        oidc_settings(),
        verifier=ObjectClaimsVerifier(values),
        clock=lambda: clock,
    )

    assert_authentication_failure(provider, "opaque-injected-token")


def test_provider_rejects_non_mapping_injected_claims() -> None:
    provider = OIDCIdentityProvider(
        oidc_settings(),
        verifier=ObjectClaimsVerifier([]),
        clock=lambda: 1_000.0,
    )

    assert_authentication_failure(provider, "opaque-injected-token")


def test_subject_mapper_rejects_invalid_inputs_and_canonicalizes_ipv6_and_ports() -> None:
    with pytest.raises(ValueError, match="subject"):
        oidc_subject_id(ISSUER, "")
    with pytest.raises(ValueError, match="issuer"):
        oidc_subject_id("relative-issuer", "subject")

    ipv6 = oidc_subject_id("https://[2001:db8::1]:443/issuer", "subject")
    assert ipv6 == oidc_subject_id("https://[2001:db8::1]/issuer", "subject")
    assert ipv6 != oidc_subject_id("https://[2001:db8::1]:8443/issuer", "subject")
    assert auth_adapters._is_valid_signing_key("unsupported", object()) is False


def test_compact_token_rejects_decoded_header_one_byte_over_limit_before_jwks() -> None:
    now = int(time.time())
    header_prefix = f'{{"alg":"RS256","kid":"{RSA_KID}"}}'
    oversized_header = header_prefix + " " * (MAX_OIDC_HEADER_BYTES + 1 - len(header_prefix))
    token = raw_json_token(
        oversized_header,
        json.dumps(claims(now), separators=(",", ":")),
    )
    client = StaticJWKClient({})

    assert_authentication_failure(provider_for(oidc_settings(), client, now=now), token)
    assert client.calls == 0


def test_incomplete_constructed_settings_fail_closed_in_adapter_constructors() -> None:
    with pytest.raises(ValueError, match="incomplete"):
        OIDCIdentityProvider(oidc_settings().model_copy(update={"oidc_issuer": None}))
    with pytest.raises(ValueError, match="incomplete"):
        PyJWTTokenVerifier(
            oidc_settings().model_copy(update={"oidc_clock_skew_seconds": None}),
            jwks_client=StaticJWKClient({}),
        )
    with pytest.raises(ValueError, match="incomplete"):
        PyJWTTokenVerifier(oidc_settings().model_copy(update={"oidc_jwks_timeout_seconds": None}))


def test_identity_provider_factory_selects_exact_mode_and_fails_closed() -> None:
    assert isinstance(
        build_identity_provider(Settings(identity_provider="deny")),
        DenyAllIdentityProvider,
    )
    assert isinstance(build_identity_provider(oidc_settings()), OIDCIdentityProvider)

    unsupported = Settings.model_construct(
        environment="test",
        identity_provider=cast(Any, "unsupported"),
    )
    with pytest.raises(ValueError, match="unsupported identity provider"):
        build_identity_provider(unsupported)

    invalid_production = Settings.model_construct(
        environment="production",
        identity_provider="deterministic",
        deterministic_admin_token=None,
        deterministic_reviewer_token=None,
    )
    with pytest.raises(ValueError, match="production identity provider"):
        build_identity_provider(invalid_production)

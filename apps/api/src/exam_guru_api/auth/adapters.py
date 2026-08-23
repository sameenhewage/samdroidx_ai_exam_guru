from dataclasses import dataclass
from secrets import compare_digest

from exam_guru_api.auth.domain import AdminRole, Principal
from exam_guru_api.auth.ports import (
    AuthenticationError,
    AuthenticationFailureCode,
    DenyAllIdentityProvider,
    IdentityProvider,
)
from exam_guru_api.core.config import Settings


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


def build_identity_provider(settings: Settings) -> IdentityProvider:
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

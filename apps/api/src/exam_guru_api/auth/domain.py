from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class AdminRole(StrEnum):
    ADMIN = "admin"
    REVIEWER = "reviewer"


class Permission(StrEnum):
    TAXONOMY_READ = "taxonomy:read"
    TAXONOMY_WRITE = "taxonomy:write"
    SOURCE_READ = "source:read"
    SOURCE_WRITE = "source:write"
    EXTRACTION_TRIGGER = "extraction:trigger"
    CONTENT_REVIEW = "content:review"
    SOURCE_TRUST = "source:trust"
    KNOWLEDGE_READ = "knowledge:read"
    KNOWLEDGE_WRITE = "knowledge:write"
    RETRIEVAL_READ = "retrieval:read"
    ANALYTICS_READ = "analytics:read"
    ANALYTICS_RUN = "analytics:run"
    BLUEPRINT_READ = "blueprint:read"
    BLUEPRINT_GENERATE = "blueprint:generate"
    GENERATION_READ = "generation:read"
    GENERATION_RUN = "generation:run"
    PAPER_PUBLISH = "paper:publish"


_ROLE_PERMISSIONS = {
    AdminRole.ADMIN: frozenset(Permission),
    AdminRole.REVIEWER: frozenset(
        {
            Permission.TAXONOMY_READ,
            Permission.SOURCE_READ,
            Permission.KNOWLEDGE_READ,
            Permission.RETRIEVAL_READ,
            Permission.ANALYTICS_READ,
            Permission.BLUEPRINT_READ,
            Permission.GENERATION_READ,
            Permission.CONTENT_REVIEW,
        }
    ),
}


@dataclass(frozen=True, slots=True)
class Principal:
    subject_id: UUID
    roles: frozenset[AdminRole]


class AuthorizationError(PermissionError):
    def __init__(self, subject_id: UUID, permission: Permission) -> None:
        self.subject_id = subject_id
        self.permission = permission
        super().__init__(f"{subject_id} lacks {permission.value}")


def authorize(principal: Principal, permission: Permission) -> Principal:
    if any(permission in _ROLE_PERMISSIONS[role] for role in principal.roles):
        return principal
    raise AuthorizationError(principal.subject_id, permission)

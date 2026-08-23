import asyncio
from collections.abc import Iterator
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.community.postgres import PostgresContainer

from exam_guru_api.curriculum.domain import TaxonomyLevel, TaxonomyNode
from exam_guru_api.curriculum.models import (
    CurriculumVersionModel,
    ExamConfigurationModel,
    MediumModel,
    TaxonomyNodeModel,
)
from exam_guru_api.curriculum.repository import SqlAlchemyTaxonomyRepository
from exam_guru_api.infrastructure.migrations import upgrade_database

PGVECTOR_IMAGE = "pgvector/pgvector:0.8.6-pg18-trixie"
ACTOR_ID = UUID(int=900)


@pytest.fixture(scope="module")
def taxonomy_database_url() -> Iterator[str]:
    with PostgresContainer(
        image=PGVECTOR_IMAGE,
        username="exam_guru",
        password="integration-only",
        dbname="exam_guru_taxonomy_test",
        driver="asyncpg",
    ) as postgres:
        database_url = postgres.get_connection_url()
        upgrade_database(database_url)
        yield database_url


async def seed_curriculum(session: AsyncSession, offset: int) -> UUID:
    exam_id = UUID(int=1_000 + offset)
    medium_id = UUID(int=2_000 + offset)
    curriculum_version_id = UUID(int=3_000 + offset)
    session.add_all(
        [
            ExamConfigurationModel(
                id=exam_id,
                code=f"G5S-{offset}",
                name="Grade 5 Scholarship Examination",
                grade=5,
                active=True,
                created_by=ACTOR_ID,
                updated_by=ACTOR_ID,
            ),
            MediumModel(
                id=medium_id,
                code=f"m{offset}",
                name=f"Medium {offset}",
                active=True,
                created_by=ACTOR_ID,
                updated_by=ACTOR_ID,
            ),
        ]
    )
    await session.flush()
    session.add(
        CurriculumVersionModel(
            id=curriculum_version_id,
            exam_configuration_id=exam_id,
            medium_id=medium_id,
            code=f"2026-{offset}",
            title=f"Curriculum {offset}",
            active=True,
            created_by=ACTOR_ID,
            updated_by=ACTOR_ID,
        )
    )
    await session.flush()
    return curriculum_version_id


def taxonomy_nodes(curriculum_version_id: UUID, offset: int) -> tuple[TaxonomyNode, ...]:
    competency = TaxonomyNode(
        id=UUID(int=10_000 + offset),
        curriculum_version_id=curriculum_version_id,
        level=TaxonomyLevel.COMPETENCY,
        code="C1",
        title="Competency 1",
    )
    skill = TaxonomyNode(
        id=UUID(int=20_000 + offset),
        curriculum_version_id=curriculum_version_id,
        level=TaxonomyLevel.SKILL,
        code="S1",
        title="Skill 1",
        parent_id=competency.id,
    )
    return competency, skill


@pytest.mark.integration
def test_taxonomy_round_trip_preserves_hierarchy_and_audit_metadata(
    taxonomy_database_url: str,
) -> None:
    async def exercise() -> None:
        engine = create_async_engine(taxonomy_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            curriculum_version_id = await seed_curriculum(session, 1)
            nodes = taxonomy_nodes(curriculum_version_id, 1)
            repository = SqlAlchemyTaxonomyRepository(session)
            await repository.add_nodes(nodes[:1], actor_id=ACTOR_ID)
            await session.commit()

        async with sessions() as session:
            repository = SqlAlchemyTaxonomyRepository(session)
            await repository.add_nodes(nodes[1:], actor_id=ACTOR_ID)
            await session.commit()

        async with sessions() as session:
            repository = SqlAlchemyTaxonomyRepository(session)
            loaded = await repository.list_nodes(curriculum_version_id)
            persisted = (
                await session.scalars(
                    select(TaxonomyNodeModel).where(
                        TaxonomyNodeModel.curriculum_version_id == curriculum_version_id
                    )
                )
            ).all()

        await engine.dispose()
        assert loaded == nodes
        assert all(node.created_by == ACTOR_ID for node in persisted)
        assert all(node.updated_by == ACTOR_ID for node in persisted)
        assert all(node.created_at is not None for node in persisted)
        assert all(node.updated_at is not None for node in persisted)

    asyncio.run(exercise())


@pytest.mark.integration
def test_database_rejects_non_grade_five_exam_configuration(
    taxonomy_database_url: str,
) -> None:
    async def exercise() -> None:
        engine = create_async_engine(taxonomy_database_url)
        sessions = async_sessionmaker(engine)
        async with sessions() as session:
            session.add(
                ExamConfigurationModel(
                    id=UUID(int=4_001),
                    code="G6S",
                    name="Invalid grade",
                    grade=6,
                    active=True,
                    created_by=ACTOR_ID,
                    updated_by=ACTOR_ID,
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()
        await engine.dispose()

    asyncio.run(exercise())


@pytest.mark.integration
def test_database_rejects_cross_curriculum_parent(taxonomy_database_url: str) -> None:
    async def exercise() -> None:
        engine = create_async_engine(taxonomy_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            first_curriculum_id = await seed_curriculum(session, 3)
            second_curriculum_id = await seed_curriculum(session, 4)
            competency = taxonomy_nodes(first_curriculum_id, 3)[0]
            repository = SqlAlchemyTaxonomyRepository(session)
            await repository.add_nodes((competency,), actor_id=ACTOR_ID)
            await session.commit()

        async with sessions() as session:
            session.add(
                TaxonomyNodeModel(
                    id=UUID(int=30_004),
                    curriculum_version_id=second_curriculum_id,
                    parent_id=competency.id,
                    level=TaxonomyLevel.SKILL,
                    code="S1",
                    title="Cross-curriculum skill",
                    active=True,
                    created_by=ACTOR_ID,
                    updated_by=ACTOR_ID,
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()
        await engine.dispose()

    asyncio.run(exercise())


@pytest.mark.integration
def test_database_rejects_active_child_with_inactive_parent(
    taxonomy_database_url: str,
) -> None:
    async def exercise() -> None:
        engine = create_async_engine(taxonomy_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            curriculum_version_id = await seed_curriculum(session, 5)
            parent = TaxonomyNodeModel(
                id=UUID(int=10_005),
                curriculum_version_id=curriculum_version_id,
                parent_id=None,
                level=TaxonomyLevel.COMPETENCY,
                code="C1",
                title="Inactive competency",
                active=False,
                created_by=ACTOR_ID,
                updated_by=ACTOR_ID,
            )
            session.add(parent)
            await session.commit()

        async with sessions() as session:
            session.add(
                TaxonomyNodeModel(
                    id=UUID(int=20_005),
                    curriculum_version_id=curriculum_version_id,
                    parent_id=parent.id,
                    level=TaxonomyLevel.SKILL,
                    code="S1",
                    title="Active skill",
                    active=True,
                    created_by=ACTOR_ID,
                    updated_by=ACTOR_ID,
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()
        await engine.dispose()

    asyncio.run(exercise())


@pytest.mark.integration
def test_database_rejects_invalid_parent_level(taxonomy_database_url: str) -> None:
    async def exercise() -> None:
        engine = create_async_engine(taxonomy_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            curriculum_version_id = await seed_curriculum(session, 6)
            parent = TaxonomyNodeModel(
                id=UUID(int=10_006),
                curriculum_version_id=curriculum_version_id,
                parent_id=None,
                level=TaxonomyLevel.COMPETENCY,
                code="C1",
                title="Competency",
                active=True,
                created_by=ACTOR_ID,
                updated_by=ACTOR_ID,
            )
            session.add(parent)
            await session.commit()

        async with sessions() as session:
            session.add(
                TaxonomyNodeModel(
                    id=UUID(int=30_006),
                    curriculum_version_id=curriculum_version_id,
                    parent_id=parent.id,
                    level=TaxonomyLevel.SUB_SKILL,
                    code="SS1",
                    title="Sub-skill with wrong parent",
                    active=True,
                    created_by=ACTOR_ID,
                    updated_by=ACTOR_ID,
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()
        await engine.dispose()

    asyncio.run(exercise())


@pytest.mark.integration
def test_database_rejects_deactivating_parent_with_active_children(
    taxonomy_database_url: str,
) -> None:
    async def exercise() -> None:
        engine = create_async_engine(taxonomy_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            curriculum_version_id = await seed_curriculum(session, 7)
            nodes = taxonomy_nodes(curriculum_version_id, 7)
            repository = SqlAlchemyTaxonomyRepository(session)
            await repository.add_nodes(nodes, actor_id=ACTOR_ID)
            await session.commit()

        async with sessions() as session:
            parent = await session.get(TaxonomyNodeModel, nodes[0].id)
            assert parent is not None
            parent.active = False
            parent.updated_by = UUID(int=901)
            with pytest.raises(IntegrityError):
                await session.commit()
        await engine.dispose()

    asyncio.run(exercise())


@pytest.mark.integration
def test_database_rejects_duplicate_sibling_code(taxonomy_database_url: str) -> None:
    async def exercise() -> None:
        engine = create_async_engine(taxonomy_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            curriculum_version_id = await seed_curriculum(session, 2)
            competency, skill = taxonomy_nodes(curriculum_version_id, 2)
            repository = SqlAlchemyTaxonomyRepository(session)
            await repository.add_nodes((competency, skill), actor_id=ACTOR_ID)
            await session.commit()

        async with sessions() as session:
            session.add(
                TaxonomyNodeModel(
                    id=UUID(int=30_002),
                    curriculum_version_id=curriculum_version_id,
                    parent_id=competency.id,
                    level=TaxonomyLevel.SKILL,
                    code=skill.code,
                    title="Duplicate skill",
                    active=True,
                    created_by=ACTOR_ID,
                    updated_by=ACTOR_ID,
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()
        await engine.dispose()

    asyncio.run(exercise())

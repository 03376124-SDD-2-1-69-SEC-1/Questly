"""An Admin approves an Instructor request through the A-01 page, on Postgres.

The app is built the way production builds it (only settings, R2 and the
vector store swapped out) and pointed at the throwaway branch, so the role
change below went through `SQLAuthRepository` and not a fake.
"""

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from questly.core.auth.models import InstructorRequest, Role, User
from questly.core.auth.service import hash_password
from questly.database.core.auth_repository import SQLAuthRepository
from questly.database.session import SessionFactory
from questly.main import create_app
from tests.fakes.app import fake_settings
from tests.fakes.documents import FakeObjectStorage
from tests.fakes.vector import FakeVectorRepository
from tests.integration.forms import asgi_client, log_in, post_form

pytestmark = pytest.mark.postgres


@pytest.mark.anyio
@pytest.mark.usefixtures("empty_core_tables")
async def test_approving_a_request_on_a1_makes_the_applicant_an_instructor(
    postgres_url: str, session_factory: SessionFactory
) -> None:
    auth = SQLAuthRepository(session_factory)
    auth.create_user(
        User(
            email="admin@kmitl.ac.th",
            full_name="Admin",
            password_hash=hash_password("admin-pass"),
            role=Role.ADMIN,
            email_verified=True,
        )
    )
    applicant = auth.create_user(
        User(
            email="anan.t@kmitl.ac.th",
            full_name="Anan Thong",
            password_hash=hash_password("applicant-pass"),
            email_verified=True,
        )
    )
    pending = auth.create_instructor_request(
        InstructorRequest(applicant.id, "Information Technology", datetime.now(UTC))
    )
    app = create_app(
        settings=replace(fake_settings(), database_url=postgres_url),
        object_storage=FakeObjectStorage(),
        vector_repository=FakeVectorRepository(),
        secure_cookies=False,
    )

    async with asgi_client(app) as client:
        await log_in(client, "admin@kmitl.ac.th", "admin-pass")
        listing = await client.get("/admin/settings")
        approved = await post_form(
            client,
            f"/admin/settings/requests/{pending.id}/approve",
            {},
            page="/admin/settings",
        )
        after = await client.get("/admin/settings")

    assert listing.status_code == 200
    assert "Anan Thong" in listing.text and 'data-state="A-01b"' not in listing.text
    assert approved.status_code == 303
    assert 'data-state="A-01b"' in after.text
    with session_factory() as session:
        role = session.execute(
            text("SELECT role FROM core.users WHERE id = :id"), {"id": applicant.id}
        ).scalar_one()
    assert role == Role.INSTRUCTOR.value

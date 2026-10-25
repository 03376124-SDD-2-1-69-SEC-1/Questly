"""AuthService use cases against the in-memory repository."""

import logging
from dataclasses import replace
from datetime import timedelta

import pytest

from questly.core.auth.models import (
    Actor,
    InstructorRequestStatus,
    Landing,
    NotAuthenticatedError,
    PermissionDeniedError,
    Role,
    User,
)
from questly.core.auth.service import (
    AccountDeactivatedError,
    AuthService,
    EmailAlreadyRegisteredError,
    EmailNotVerifiedError,
    FacultyRequiredError,
    InstructorRequestExistsError,
    InvalidCredentialsError,
    NotKmitlEmailError,
    PasswordMismatchError,
    PasswordTooShortError,
    VerificationLinkExpiredError,
    VerificationLinkInvalidError,
    hash_password,
    verify_password,
)
from questly.core.notifications.service import NotificationService
from questly.integrations.email import StubEmailSender
from tests.fakes.auth import (
    DEFAULT_PASSWORD,
    FakeAuthRepository,
    FakeClock,
    seed_user,
    verification_path,
)
from tests.fakes.classrooms import FakeClassroomRepository
from tests.fakes.notifications import FakeNotificationRepository

PASSWORD = "correct horse"


@pytest.fixture()
def repository() -> FakeAuthRepository:
    return FakeAuthRepository()


@pytest.fixture()
def mailer() -> StubEmailSender:
    return StubEmailSender()


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture()
def service(
    repository: FakeAuthRepository, mailer: StubEmailSender, clock: FakeClock
) -> AuthService:
    notification_service = NotificationService(
        FakeNotificationRepository(),
        mailer,
        clock,
        FakeClassroomRepository(),
        repository,
        base_url="http://localhost:8000",
    )
    return AuthService(
        repository, notification_service, clock, base_url="http://localhost:8000"
    )


def _sign_up(
    service: AuthService,
    *,
    email: str = "somchai@kmitl.ac.th",
    wants_instructor: bool = False,
    faculty: str = "",
) -> User:
    return service.sign_up(
        full_name="Somchai Prasert",
        email=email,
        password=PASSWORD,
        confirm_password=PASSWORD,
        wants_instructor=wants_instructor,
        faculty=faculty,
    )


def _last_token(mailer: StubEmailSender) -> str:
    return verification_path(mailer).split("token=", 1)[1]


def test_sign_up_creates_an_unverified_student_and_emails_a_link(
    service: AuthService, mailer: StubEmailSender
) -> None:
    user = _sign_up(service, email="  Somchai@KMITL.ac.th ")

    assert user.role is Role.STUDENT
    assert user.email == "somchai@kmitl.ac.th"
    assert not user.email_verified
    assert mailer.messages[-1].to == "somchai@kmitl.ac.th"
    assert "Hi Somchai Prasert," in mailer.messages[-1].body
    assert verification_path(mailer) in mailer.messages[-1].body


def test_sign_up_rejects_a_non_kmitl_email(service: AuthService) -> None:
    with pytest.raises(NotKmitlEmailError):
        _sign_up(service, email="somchai@gmail.com")


def test_sign_up_rejects_an_already_registered_email(service: AuthService) -> None:
    _sign_up(service)

    with pytest.raises(EmailAlreadyRegisteredError):
        _sign_up(service)


def test_sign_up_rejects_a_short_password(service: AuthService) -> None:
    with pytest.raises(PasswordTooShortError):
        service.sign_up(
            full_name="A",
            email="a@kmitl.ac.th",
            password="short",
            confirm_password="short",
            wants_instructor=False,
            faculty="",
        )


def test_sign_up_rejects_mismatched_passwords(service: AuthService) -> None:
    with pytest.raises(PasswordMismatchError):
        service.sign_up(
            full_name="A",
            email="a@kmitl.ac.th",
            password=PASSWORD,
            confirm_password=PASSWORD + "x",
            wants_instructor=False,
            faculty="",
        )


def test_instructor_sign_up_requires_a_faculty(service: AuthService) -> None:
    with pytest.raises(FacultyRequiredError):
        _sign_up(service, wants_instructor=True)


def test_instructor_sign_up_files_a_pending_request_and_stays_student(
    service: AuthService, repository: FakeAuthRepository
) -> None:
    user = _sign_up(service, wants_instructor=True, faculty="Engineering")

    pending = repository.list_instructor_requests(InstructorRequestStatus.PENDING)
    assert user.role is Role.STUDENT
    assert [(r.user_id, r.faculty) for r in pending] == [(user.id, "Engineering")]


def test_verify_email_marks_the_account_verified_once(
    service: AuthService, mailer: StubEmailSender
) -> None:
    _sign_up(service)
    token = _last_token(mailer)

    assert service.verify_email(token).email_verified
    with pytest.raises(VerificationLinkInvalidError):
        service.verify_email(token)


def test_verify_email_rejects_a_link_older_than_24_hours(
    service: AuthService, mailer: StubEmailSender, clock: FakeClock
) -> None:
    _sign_up(service)
    clock.advance(timedelta(hours=24))

    with pytest.raises(VerificationLinkExpiredError):
        service.verify_email(_last_token(mailer))


def test_resend_is_silent_for_unknown_emails_and_sends_for_unverified(
    service: AuthService, mailer: StubEmailSender
) -> None:
    service.resend_verification("nobody@kmitl.ac.th")
    assert mailer.messages == []

    _sign_up(service)
    service.resend_verification("somchai@kmitl.ac.th")
    assert len(mailer.messages) == 2


def test_log_in_rejects_a_wrong_password_and_an_unknown_email_alike(
    service: AuthService, repository: FakeAuthRepository
) -> None:
    seed_user(repository, email="a@kmitl.ac.th", full_name="A")

    with pytest.raises(InvalidCredentialsError):
        service.log_in(email="a@kmitl.ac.th", password="wrong password")
    with pytest.raises(InvalidCredentialsError):
        service.log_in(email="b@kmitl.ac.th", password=DEFAULT_PASSWORD)


def test_log_in_refuses_an_unverified_email(
    service: AuthService, repository: FakeAuthRepository
) -> None:
    seed_user(repository, email="a@kmitl.ac.th", full_name="A", verified=False)

    with pytest.raises(EmailNotVerifiedError):
        service.log_in(email="a@kmitl.ac.th", password=DEFAULT_PASSWORD)


def test_log_in_refuses_a_deactivated_account(
    service: AuthService, repository: FakeAuthRepository
) -> None:
    user = seed_user(repository, email="a@kmitl.ac.th", full_name="A")
    repository.update_user(replace(user, is_active=False))

    with pytest.raises(AccountDeactivatedError):
        service.log_in(email="a@kmitl.ac.th", password=DEFAULT_PASSWORD)


@pytest.mark.parametrize(
    ("role", "landing"),
    [
        (Role.STUDENT, Landing.CLASSROOMS),
        (Role.INSTRUCTOR, Landing.CLASSROOMS),
        (Role.ADMIN, Landing.ADMIN_SETTINGS),
    ],
)
def test_log_in_lands_admins_on_settings_and_others_on_classrooms(
    service: AuthService, repository: FakeAuthRepository, role: Role, landing: Landing
) -> None:
    seed_user(repository, email="a@kmitl.ac.th", full_name="A", role=role)

    result = service.log_in(email="a@kmitl.ac.th", password=DEFAULT_PASSWORD)

    assert result.landing is landing
    assert result.token


def test_resolve_session_returns_the_actor_until_logout(
    service: AuthService, repository: FakeAuthRepository
) -> None:
    user = seed_user(repository, email="a@kmitl.ac.th", full_name="A")
    token = service.log_in(email="a@kmitl.ac.th", password=DEFAULT_PASSWORD).token

    actor = service.resolve_session(token)
    service.log_out(token)

    assert (actor.user_id, actor.role) == (user.id, Role.STUDENT)
    with pytest.raises(NotAuthenticatedError):
        service.resolve_session(token)


def test_resolve_session_rejects_unknown_and_expired_tokens(
    service: AuthService, repository: FakeAuthRepository, clock: FakeClock
) -> None:
    seed_user(repository, email="a@kmitl.ac.th", full_name="A")
    token = service.log_in(email="a@kmitl.ac.th", password=DEFAULT_PASSWORD).token

    with pytest.raises(NotAuthenticatedError):
        service.resolve_session("not-a-token")
    clock.advance(timedelta(days=14))
    with pytest.raises(NotAuthenticatedError):
        service.resolve_session(token)


def test_resolve_session_rejects_a_deactivated_account(
    service: AuthService, repository: FakeAuthRepository
) -> None:
    user = seed_user(repository, email="a@kmitl.ac.th", full_name="A")
    token = service.log_in(email="a@kmitl.ac.th", password=DEFAULT_PASSWORD).token
    repository.update_user(replace(user, is_active=False))

    with pytest.raises(NotAuthenticatedError):
        service.resolve_session(token)


def test_resolve_session_writes_last_active_at_most_hourly(
    service: AuthService, repository: FakeAuthRepository, clock: FakeClock
) -> None:
    user = seed_user(repository, email="a@kmitl.ac.th", full_name="A")
    token = service.log_in(email="a@kmitl.ac.th", password=DEFAULT_PASSWORD).token
    first_seen = clock.now()

    service.resolve_session(token)
    clock.advance(timedelta(minutes=30))
    service.resolve_session(token)
    assert repository.get_user(user.id).last_active_at == first_seen

    clock.advance(timedelta(minutes=30))
    service.resolve_session(token)
    assert repository.get_user(user.id).last_active_at == clock.now()


def test_request_instructor_files_one_pending_request_for_a_student(
    service: AuthService, repository: FakeAuthRepository
) -> None:
    seed_user(repository, email="a@kmitl.ac.th", full_name="A")
    actor = service.resolve_session(
        service.log_in(email="a@kmitl.ac.th", password=DEFAULT_PASSWORD).token
    )

    created = service.request_instructor(actor, faculty="Science")

    assert created.status is InstructorRequestStatus.PENDING
    with pytest.raises(InstructorRequestExistsError):
        service.request_instructor(actor, faculty="Science")


def test_request_instructor_is_denied_to_an_instructor(
    service: AuthService, repository: FakeAuthRepository
) -> None:
    seed_user(repository, email="t@kmitl.ac.th", full_name="T", role=Role.INSTRUCTOR)
    actor = service.resolve_session(
        service.log_in(email="t@kmitl.ac.th", password=DEFAULT_PASSWORD).token
    )

    with pytest.raises(PermissionDeniedError):
        service.request_instructor(actor, faculty="Science")


def test_summaries_skip_unknown_ids_and_derive_student_numbers(
    service: AuthService, repository: FakeAuthRepository
) -> None:
    student = seed_user(repository, email="66010001@kmitl.ac.th", full_name="N")
    staff = seed_user(repository, email="somchai.p@kmitl.ac.th", full_name="S")

    summaries = service.summaries([student.id, 999, staff.id])

    assert [s.student_number for s in summaries] == ["66010001", None]


def test_password_hash_round_trips_and_uses_a_fresh_salt() -> None:
    first = hash_password(PASSWORD)

    assert verify_password(PASSWORD, first)
    assert not verify_password("wrong", first)
    assert first != hash_password(PASSWORD)


def test_each_session_has_its_own_csrf_token_until_logout(
    service: AuthService, repository: FakeAuthRepository
) -> None:
    seed_user(repository, email="a@kmitl.ac.th", full_name="A")
    first = service.log_in(email="a@kmitl.ac.th", password=DEFAULT_PASSWORD).token
    second = service.log_in(email="a@kmitl.ac.th", password=DEFAULT_PASSWORD).token

    assert service.csrf_token_for(first) != service.csrf_token_for(second)
    service.log_out(first)
    with pytest.raises(NotAuthenticatedError):
        service.csrf_token_for(first)


@pytest.mark.parametrize(
    ("role", "landing"),
    [(Role.ADMIN, Landing.ADMIN_SETTINGS), (Role.STUDENT, Landing.CLASSROOMS)],
)
def test_landing_for_follows_the_role(
    service: AuthService, role: Role, landing: Landing
) -> None:
    actor = Actor(user_id=1, role=role, full_name="A", email="a@kmitl.ac.th")

    assert service.landing_for(actor) is landing


@pytest.mark.parametrize("role", [Role.STUDENT, Role.INSTRUCTOR])
def test_admin_home_is_admin_only(
    service: AuthService, repository: FakeAuthRepository, role: Role
) -> None:
    user = seed_user(repository, email="x@kmitl.ac.th", full_name="X", role=role)
    actor = Actor(user_id=user.id, role=role, full_name="X", email=user.email)

    with pytest.raises(PermissionDeniedError):
        service.admin_home(actor)


def test_admin_home_returns_the_admin(
    service: AuthService, repository: FakeAuthRepository
) -> None:
    user = seed_user(
        repository, email="admin@kmitl.ac.th", full_name="Admin", role=Role.ADMIN
    )
    actor = Actor(user_id=user.id, role=Role.ADMIN, full_name="Admin", email=user.email)

    assert service.admin_home(actor).id == user.id


def test_stub_email_sender_logs_the_verify_link(
    service: AuthService, mailer: StubEmailSender, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="questly.email"):
        _sign_up(service)

    assert verification_path(mailer) in caplog.text

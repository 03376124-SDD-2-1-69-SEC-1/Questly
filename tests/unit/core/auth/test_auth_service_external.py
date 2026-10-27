"""External sign-up and on-screen verification links (OPS-43, ADR-0013)."""

import pytest

from questly.core.auth.models import Role, User
from questly.core.auth.service import (
    NO_LINK,
    AuthService,
    ExternalCannotRequestInstructorError,
    InvalidEmailError,
    LinkDisplay,
    NotKmitlEmailError,
    ShownLink,
    UseKmitlOptionError,
    VerificationEmailNotSentError,
)
from questly.core.notifications.ports import EmailDeliveryError, EmailSender
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


class FailingSender:
    def send(self, *, to: str, subject: str, body: str) -> None:
        raise EmailDeliveryError("provider unreachable")


def _service(
    repository: FakeAuthRepository,
    mailer: EmailSender,
    *,
    show_demo_link: bool = False,
    onscreen_verify_domains: tuple[str, ...] = (),
) -> AuthService:
    clock = FakeClock()
    notifications = NotificationService(
        FakeNotificationRepository(),
        mailer,
        clock,
        FakeClassroomRepository(),
        repository,
        base_url="http://localhost:8000",
    )
    return AuthService(
        repository,
        notifications,
        clock,
        base_url="http://localhost:8000",
        show_demo_link=show_demo_link,
        onscreen_verify_domains=onscreen_verify_domains,
    )


@pytest.fixture()
def repository() -> FakeAuthRepository:
    return FakeAuthRepository()


@pytest.fixture()
def mailer() -> StubEmailSender:
    return StubEmailSender()


@pytest.fixture()
def service(repository: FakeAuthRepository, mailer: StubEmailSender) -> AuthService:
    return _service(repository, mailer)


def _sign_up(
    service: AuthService,
    email: str,
    *,
    external: bool,
    wants_instructor: bool = False,
    faculty: str = "",
) -> User:
    return service.sign_up(
        full_name="Ada Lovelace",
        email=email,
        password=PASSWORD,
        confirm_password=PASSWORD,
        wants_instructor=wants_instructor,
        faculty=faculty,
        external=external,
    )


def test_external_sign_up_creates_a_normalised_student_and_emails_it(
    service: AuthService, mailer: StubEmailSender
) -> None:
    user = _sign_up(service, "  Ada@Example.COM ", external=True)

    assert user.role is Role.STUDENT
    assert user.email == "ada@example.com"
    assert [message.to for message in mailer.messages] == ["ada@example.com"]


def test_external_sign_up_refuses_a_kmitl_address(
    service: AuthService, repository: FakeAuthRepository
) -> None:
    with pytest.raises(UseKmitlOptionError):
        _sign_up(service, " Somchai@KMITL.ac.th ", external=True)

    assert repository.find_user_by_email("somchai@kmitl.ac.th") is None


def test_external_sign_up_cannot_request_instructor_rights(
    service: AuthService, repository: FakeAuthRepository
) -> None:
    with pytest.raises(ExternalCannotRequestInstructorError):
        _sign_up(
            service,
            "ada@example.com",
            external=True,
            wants_instructor=True,
            faculty="Science",
        )

    assert repository.find_user_by_email("ada@example.com") is None


@pytest.mark.parametrize(
    "email",
    [
        "ada.example.com",
        "ada@b@example.com",
        "@example.com",
        "ada@localhost",
        "ada lovelace@example.com",
        "ada@exa\tmple.com",
        "ada@example..com",
        "ada@.example.com",
        "ada@example.com.",
        "a" * 243 + "@example.com",
    ],
)
def test_external_sign_up_refuses_an_invalid_email(
    service: AuthService, repository: FakeAuthRepository, email: str
) -> None:
    with pytest.raises(InvalidEmailError):
        _sign_up(service, email, external=True)

    assert repository.list_users() == []


def test_external_sign_up_accepts_an_email_of_exactly_254_characters(
    service: AuthService,
) -> None:
    email = "a" * 242 + "@example.com"
    assert len(email) == 254

    assert _sign_up(service, email, external=True).email == email


def test_a_kmitl_subdomain_is_external_not_kmitl(service: AuthService) -> None:
    with pytest.raises(NotKmitlEmailError):
        _sign_up(service, "ada@mail.kmitl.ac.th", external=False)

    user = _sign_up(service, "ada@mail.kmitl.ac.th", external=True)
    assert user.email == "ada@mail.kmitl.ac.th"


def test_kmitl_sign_up_still_refuses_an_outside_address(service: AuthService) -> None:
    with pytest.raises(NotKmitlEmailError):
        _sign_up(service, "ada@example.com", external=False)


def test_an_external_student_cannot_request_instructor_rights_later(
    service: AuthService, repository: FakeAuthRepository
) -> None:
    seed_user(repository, email="ada@example.com", full_name="Ada")
    actor = service.resolve_session(
        service.log_in(email="ada@example.com", password=DEFAULT_PASSWORD).token
    )

    with pytest.raises(ExternalCannotRequestInstructorError):
        service.request_instructor(actor, faculty="Science")


def test_a_listed_domain_keeps_the_emailed_link_for_on_screen_use(
    repository: FakeAuthRepository, mailer: StubEmailSender
) -> None:
    service = _service(repository, mailer, onscreen_verify_domains=("kmitl.ac.th",))
    _sign_up(service, "somchai@kmitl.ac.th", external=False)

    assert service.shown_link_for(" Somchai@KMITL.ac.th") == ShownLink(
        LinkDisplay.ONSCREEN, verification_path(mailer)
    )


def test_an_unlisted_domain_keeps_no_link(
    repository: FakeAuthRepository, mailer: StubEmailSender
) -> None:
    service = _service(repository, mailer, onscreen_verify_domains=("kmitl.ac.th",))
    _sign_up(service, "ada@example.com", external=True)
    _sign_up(service, "bob@mail.kmitl.ac.th", external=True)

    assert service.shown_link_for("ada@example.com") == NO_LINK
    assert service.shown_link_for("bob@mail.kmitl.ac.th") == NO_LINK


def test_the_demo_flag_keeps_other_domains_as_demo_links(
    repository: FakeAuthRepository, mailer: StubEmailSender
) -> None:
    service = _service(
        repository,
        mailer,
        show_demo_link=True,
        onscreen_verify_domains=("kmitl.ac.th",),
    )
    _sign_up(service, "ada@example.com", external=True)
    ada_link = verification_path(mailer)
    _sign_up(service, "somchai@kmitl.ac.th", external=False)

    assert service.shown_link_for("ada@example.com") == ShownLink(
        LinkDisplay.DEMO, ada_link
    )
    assert service.shown_link_for("somchai@kmitl.ac.th").display is (
        LinkDisplay.ONSCREEN
    )


def test_the_link_is_kept_when_the_email_fails(
    repository: FakeAuthRepository,
) -> None:
    service = _service(
        repository, FailingSender(), onscreen_verify_domains=("kmitl.ac.th",)
    )

    with pytest.raises(VerificationEmailNotSentError):
        _sign_up(service, "somchai@kmitl.ac.th", external=False)

    link = service.shown_link_for("somchai@kmitl.ac.th")
    assert link.display is LinkDisplay.ONSCREEN
    token = link.url.split("token=", 1)[1]
    assert service.verify_email(token).email_verified


def test_a_resend_replaces_the_link_and_verifying_forgets_it(
    repository: FakeAuthRepository, mailer: StubEmailSender
) -> None:
    service = _service(repository, mailer, onscreen_verify_domains=("kmitl.ac.th",))
    _sign_up(service, "somchai@kmitl.ac.th", external=False)
    first = service.shown_link_for("somchai@kmitl.ac.th").url

    service.resend_verification("somchai@kmitl.ac.th")
    fresh = service.shown_link_for("somchai@kmitl.ac.th").url

    assert fresh != first
    assert fresh == verification_path(mailer)
    service.verify_email(fresh.split("token=", 1)[1])
    assert service.shown_link_for("somchai@kmitl.ac.th") == NO_LINK

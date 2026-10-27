"""Account use cases independent from HTTP and database technology."""

import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass, replace
from datetime import timedelta
from enum import StrEnum

from questly.core.auth.models import (
    Actor,
    InstructorRequest,
    Landing,
    LoginResult,
    NotAuthenticatedError,
    PermissionDeniedError,
    Role,
    Session,
    User,
    UserSummary,
    VerificationToken,
)
from questly.core.auth.ports import AuthRepository, Clock
from questly.core.notifications.ports import EmailDeliveryError
from questly.core.notifications.service import NotificationService

logger = logging.getLogger(__name__)

KMITL_DOMAIN = "@kmitl.ac.th"
MAX_EMAIL_LENGTH = 254
MIN_PASSWORD_LENGTH = 8
VERIFICATION_TTL = timedelta(hours=24)
SESSION_TTL = timedelta(days=14)
LAST_ACTIVE_RESOLUTION = timedelta(hours=1)

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1


class NotKmitlEmailError(Exception):
    """G-03a: only @kmitl.ac.th addresses may sign up."""


class UseKmitlOptionError(Exception):
    """An external sign-up with an @kmitl.ac.th address (ADR-0013)."""


class InvalidEmailError(Exception):
    """An external sign-up whose address is not a plausible email."""


class ExternalCannotRequestInstructorError(Exception):
    """Only @kmitl.ac.th accounts may ask for Instructor rights (ADR-0013)."""


class EmailAlreadyRegisteredError(Exception):
    """G-03b."""


class PasswordMismatchError(Exception):
    """Password and confirmation differ."""


class PasswordTooShortError(Exception):
    """Password shorter than MIN_PASSWORD_LENGTH."""


class FacultyRequiredError(Exception):
    """ "I am an instructor" ticked without a faculty."""


class InvalidCredentialsError(Exception):
    """G-01a: unknown email or wrong password; never says which."""


class EmailNotVerifiedError(Exception):
    """G-01b."""


class AccountDeactivatedError(Exception):
    """An Admin deactivated this account."""


class VerificationLinkInvalidError(Exception):
    """Unknown or already used verification link."""


class VerificationLinkExpiredError(Exception):
    """G-04c: the link is older than 24 hours."""


class InstructorRequestExistsError(Exception):
    """The account already has a pending Instructor request."""


class VerificationEmailNotSentError(Exception):
    """The account and its verification token exist, but the email did not go out.

    `user` is the account, so a caller can carry on as if the sign-up worked and
    offer a resend.
    """

    def __init__(self, user: User) -> None:
        super().__init__("verification email could not be sent")
        self.user = user


class RateLimitBucket(StrEnum):
    """The auth requests a RateLimiter counts, each with its own limit (OPS-44)."""

    LOGIN = "login"
    SIGNUP = "signup"
    RESEND = "resend"


class RateLimitedError(Exception):
    """A client used up its RateLimiter bucket; it may retry after the delay."""

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(f"rate limited, retry after {retry_after_seconds}s")
        self.retry_after_seconds = retry_after_seconds


class LinkDisplay(StrEnum):
    """How G-04a shows a kept verification link, if at all."""

    HIDDEN = "hidden"
    # the stub sender outside production mails nothing (OPS-37)
    DEMO = "demo"
    # the domain's mail system filters our mail (ADR-0013)
    ONSCREEN = "onscreen"


@dataclass(frozen=True, slots=True)
class ShownLink:
    display: LinkDisplay
    url: str = ""


NO_LINK = ShownLink(LinkDisplay.HIDDEN)


class AuthService:
    """Sign-up, verification, login and session resolution."""

    def __init__(
        self,
        repository: AuthRepository,
        notification_service: NotificationService,
        clock: Clock,
        *,
        base_url: str,
        show_demo_link: bool = False,
        onscreen_verify_domains: tuple[str, ...] = (),
    ) -> None:
        self._repository = repository
        self._notification_service = notification_service
        self._clock = clock
        self._base_url = base_url.rstrip("/")
        self._show_demo_link = show_demo_link
        self._onscreen_domains = frozenset(
            domain.lower() for domain in onscreen_verify_domains
        )
        self._latest_links: dict[str, str] = {}

    def sign_up(
        self,
        *,
        full_name: str,
        email: str,
        password: str,
        confirm_password: str,
        wants_instructor: bool,
        faculty: str,
        external: bool = False,
    ) -> User:
        """Create a Student account and send its verification link.

        `external` is the "บุคคลภายนอก" choice on G-03: any valid address
        outside @kmitl.ac.th, Student only (ADR-0013).
        """
        email = _normalize_email(email)
        full_name = full_name.strip()
        faculty = faculty.strip()
        if not full_name:
            raise ValueError("full name must not be blank")
        if external:
            if _is_kmitl(email):
                raise UseKmitlOptionError
            if not _is_valid_email(email):
                raise InvalidEmailError
            if wants_instructor:
                raise ExternalCannotRequestInstructorError
        elif not _is_kmitl(email):
            raise NotKmitlEmailError
        if len(password) < MIN_PASSWORD_LENGTH:
            raise PasswordTooShortError
        if password != confirm_password:
            raise PasswordMismatchError
        if wants_instructor and not faculty:
            raise FacultyRequiredError
        if self._repository.find_user_by_email(email) is not None:
            raise EmailAlreadyRegisteredError

        user = self._repository.create_user(
            User(
                email=email, full_name=full_name, password_hash=hash_password(password)
            )
        )
        if wants_instructor:
            self._repository.create_instructor_request(
                InstructorRequest(
                    user_id=user.id, faculty=faculty, requested_at=self._clock.now()
                )
            )
        self._send_verification(user)
        return user

    def verify_email(self, token: str) -> User:
        stored = self._repository.find_verification_token(_digest(token))
        if stored is None or stored.used:
            raise VerificationLinkInvalidError
        if stored.expires_at <= self._clock.now():
            raise VerificationLinkExpiredError
        self._repository.update_verification_token(replace(stored, used=True))
        user = self._require_user(stored.user_id)
        self._latest_links.pop(user.email, None)
        return self._repository.update_user(replace(user, email_verified=True))

    def resend_verification(self, email: str) -> None:
        """Send a fresh link. Silent for unknown or verified emails (no probing)."""
        user = self._repository.find_user_by_email(_normalize_email(email))
        if user is None or user.email_verified:
            return
        self._send_verification(user)

    def shown_link_for(self, email: str) -> ShownLink:
        """The latest verification link of `email` and how G-04a shows it.

        Links are kept in memory only for an email in an on-screen domain, or
        for every email when the composition root turned on `show_demo_link`
        (the stub sender outside production).
        """
        email = _normalize_email(email)
        if email not in self._latest_links:
            return NO_LINK
        display = LinkDisplay.ONSCREEN if self._is_onscreen(email) else LinkDisplay.DEMO
        return ShownLink(display, self._latest_links[email])

    def log_in(self, *, email: str, password: str) -> LoginResult:
        user = self._repository.find_user_by_email(_normalize_email(email))
        if user is None or not verify_password(password, user.password_hash):
            raise InvalidCredentialsError
        if not user.is_active:
            raise AccountDeactivatedError
        if not user.email_verified:
            raise EmailNotVerifiedError
        token = secrets.token_urlsafe(32)
        self._repository.create_session(
            Session(
                token_hash=_digest(token),
                user_id=user.id,
                expires_at=self._clock.now() + SESSION_TTL,
                csrf_token=secrets.token_urlsafe(32),
            )
        )
        return LoginResult(token=token, user=user, landing=_landing(user.role))

    def log_out(self, token: str) -> None:
        session = self._repository.find_session(_digest(token))
        if session is None:
            return
        self._repository.update_session(replace(session, revoked=True))

    def resolve_session(self, token: str) -> Actor:
        """Turn a session cookie into the Actor, or raise NotAuthenticatedError."""
        session = self._live_session(token)
        now = self._clock.now()
        user = self._repository.get_user(session.user_id)
        if user is None or not user.is_active:
            raise NotAuthenticatedError
        if (
            user.last_active_at is None
            or now - user.last_active_at >= LAST_ACTIVE_RESOLUTION
        ):
            user = self._repository.update_user(replace(user, last_active_at=now))
        return Actor(
            user_id=user.id, role=user.role, full_name=user.full_name, email=user.email
        )

    def csrf_token_for(self, token: str) -> str:
        """The synchronizer token of a live session, or NotAuthenticatedError."""
        return self._live_session(token).csrf_token

    def landing_for(self, actor: Actor) -> Landing:
        """Where `/` sends a logged-in account."""
        return _landing(actor.role)

    def admin_home(self, actor: Actor) -> User:
        """The Admin landing; A-01 replaces its page when the admin slice lands."""
        if actor.role is not Role.ADMIN:
            raise PermissionDeniedError
        return self._require_user(actor.user_id)

    def me(self, actor: Actor) -> User:
        return self._require_user(actor.user_id)

    def request_instructor(self, actor: Actor, *, faculty: str) -> InstructorRequest:
        faculty = faculty.strip()
        if actor.role is not Role.STUDENT:
            raise PermissionDeniedError
        if not _is_kmitl(actor.email):
            raise ExternalCannotRequestInstructorError
        if not faculty:
            raise FacultyRequiredError
        if self._repository.find_pending_instructor_request(actor.user_id) is not None:
            raise InstructorRequestExistsError
        return self._repository.create_instructor_request(
            InstructorRequest(
                user_id=actor.user_id, faculty=faculty, requested_at=self._clock.now()
            )
        )

    def summaries(self, user_ids: list[int]) -> list[UserSummary]:
        """Name and email of each known account, for other slices' views."""
        summaries = []
        for user_id in user_ids:
            user = self._repository.get_user(user_id)
            if user is None:
                continue
            summaries.append(
                UserSummary(user_id=user.id, full_name=user.full_name, email=user.email)
            )
        return summaries

    def _send_verification(self, user: User) -> None:
        """Store a fresh token and mail its link.

        Raises VerificationEmailNotSentError when the mail provider fails; the
        token stays stored, so a later resend is all the account needs.
        """
        token = secrets.token_urlsafe(32)
        verify_path = f"{self._base_url}/verify?token={token}"

        self._repository.create_verification_token(
            VerificationToken(
                token_hash=_digest(token),
                user_id=user.id,
                expires_at=self._clock.now() + VERIFICATION_TTL,
            )
        )
        if self._show_demo_link or self._is_onscreen(user.email):
            self._latest_links[user.email] = verify_path
        try:
            self._notification_service.send_email(
                to=user.email,
                subject="Verify your Questly email",
                body=(
                    f"Hi {user.full_name},\n\n"
                    "Open this link to verify your email:\n\n"
                    f"{verify_path}\n"
                ),
            )
        except EmailDeliveryError as exc:
            logger.warning(
                "verification email for user %s was not sent: %s", user.id, exc
            )
            raise VerificationEmailNotSentError(user) from exc

    def _is_onscreen(self, email: str) -> bool:
        return email.rpartition("@")[2] in self._onscreen_domains

    def _live_session(self, token: str) -> Session:
        session = self._repository.find_session(_digest(token))
        if (
            session is None
            or session.revoked
            or session.expires_at <= self._clock.now()
        ):
            raise NotAuthenticatedError
        return session

    def _require_user(self, user_id: int) -> User:
        user = self._repository.get_user(user_id)
        if user is None:
            raise NotAuthenticatedError
        return user


def _landing(role: Role) -> Landing:
    return Landing.ADMIN_SETTINGS if role is Role.ADMIN else Landing.CLASSROOMS


def hash_password(password: str) -> str:
    """Return `scrypt$n$r$p$salt$hash`, hex-encoded, with a fresh 16-byte salt."""
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${derived.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    scheme, n, r, p, salt_hex, hash_hex = encoded.split("$")
    if scheme != "scrypt":
        return False
    derived = hashlib.scrypt(
        password.encode(), salt=bytes.fromhex(salt_hex), n=int(n), r=int(r), p=int(p)
    )
    return hmac.compare_digest(derived.hex(), hash_hex)


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _is_kmitl(email: str) -> bool:
    """Exactly @kmitl.ac.th; a subdomain such as @mail.kmitl.ac.th is not."""
    return email.endswith(KMITL_DOMAIN)


def _is_valid_email(email: str) -> bool:
    """One @, a local part, a dotted domain with no empty label, no whitespace."""
    if len(email) > MAX_EMAIL_LENGTH or any(c.isspace() for c in email):
        return False
    local, at, domain = email.partition("@")
    if not at or not local or "@" in domain or "." not in domain:
        return False
    return all(domain.split("."))

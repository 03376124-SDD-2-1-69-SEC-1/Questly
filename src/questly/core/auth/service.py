"""Account use cases independent from HTTP and database technology."""

import hashlib
import hmac
import secrets
from dataclasses import replace
from datetime import timedelta

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
from questly.core.notifications.service import NotificationService

KMITL_DOMAIN = "@kmitl.ac.th"
MIN_PASSWORD_LENGTH = 8
VERIFICATION_TTL = timedelta(hours=24)
SESSION_TTL = timedelta(days=14)
LAST_ACTIVE_RESOLUTION = timedelta(hours=1)

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1


class NotKmitlEmailError(Exception):
    """G-03a: only @kmitl.ac.th addresses may sign up."""


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


class AuthService:
    """Sign-up, verification, login and session resolution."""

    def __init__(
        self,
        repository: AuthRepository,
        notification_service: NotificationService,
        clock: Clock,
        *,
        base_url: str = "http://localhost:8000",
    ) -> None:
        self._repository = repository
        self._notification_service = notification_service
        self._clock = clock
        self._base_url = base_url.rstrip("/")

    def sign_up(
        self,
        *,
        full_name: str,
        email: str,
        password: str,
        confirm_password: str,
        wants_instructor: bool,
        faculty: str,
    ) -> User:
        email = _normalize_email(email)
        full_name = full_name.strip()
        faculty = faculty.strip()
        if not full_name:
            raise ValueError("full name must not be blank")
        if not email.endswith(KMITL_DOMAIN):
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
        return self._repository.update_user(replace(user, email_verified=True))

    def resend_verification(self, email: str) -> None:
        """Send a fresh link. Silent for unknown or verified emails (no probing)."""
        user = self._repository.find_user_by_email(_normalize_email(email))
        if user is None or user.email_verified:
            return
        self._send_verification(user)

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
        token = secrets.token_urlsafe(32)
        verify_path = f"{self._base_url}/verify?token={token}"

        self._repository.create_verification_token(
            VerificationToken(
                token_hash=_digest(token),
                user_id=user.id,
                expires_at=self._clock.now() + VERIFICATION_TTL,
            )
        )
        self._notification_service.send_email(
            to=user.email,
            subject="Verify your Questly email",
            body=(
                f"Hi {user.full_name},\n\n"
                "Open this link to verify your email:\n\n"
                f"{verify_path}\n"
            ),
        )

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

"""Ports the auth slice needs.

`tests/fakes/auth.py` provides the in-memory AuthRepository; the SQL adapter
arrives with OPS-15. main.py fills VerificationMailer with the email stub
until the notifications slice owns sending (ADR-0007 §6.4) — swapping it
changes only main.py.
"""

from datetime import datetime
from typing import Protocol

from questly.core.auth.models import (
    InstructorRequest,
    InstructorRequestStatus,
    Session,
    User,
    VerificationToken,
)


class AuthRepository(Protocol):
    """Accounts, sessions, verification tokens and Instructor requests.

    Each `create_*` is the only operation that assigns an id.
    """

    def get_user(self, user_id: int) -> User | None: ...

    def find_user_by_email(self, email: str) -> User | None: ...

    def list_users(self) -> list[User]: ...

    def search_users(
        self,
        *,
        q: str | None = None,
        role: str | None = None,
        page: int = 1,
        page_size: int = 25,
    ) -> tuple[list[User], int]: ...

    def create_user(self, user: User) -> User: ...

    def update_user(self, user: User) -> User: ...

    def create_session(self, session: Session) -> Session: ...

    def find_session(self, token_hash: str) -> Session | None: ...

    def update_session(self, session: Session) -> Session: ...

    def revoke_sessions_of(self, user_id: int) -> None: ...

    def create_verification_token(
        self, token: VerificationToken
    ) -> VerificationToken: ...

    def find_verification_token(self, token_hash: str) -> VerificationToken | None: ...

    def update_verification_token(
        self, token: VerificationToken
    ) -> VerificationToken: ...

    def create_instructor_request(
        self, request: InstructorRequest
    ) -> InstructorRequest: ...

    def list_instructor_requests(
        self, status: InstructorRequestStatus | None = None
    ) -> list[InstructorRequest]: ...

    def review_instructor_request(
        self, request_id: int, reviewer_id: int, approve: bool
    ) -> InstructorRequest:
        """Approve (promoting the applicant) or reject a pending request.

        Raises InstructorRequestNotFoundError for an unknown id and
        InstructorRequestAlreadyReviewedError when it is no longer pending.
        """
        ...

    def find_pending_instructor_request(
        self, user_id: int
    ) -> InstructorRequest | None: ...


class RateLimiter(Protocol):
    """Counts auth requests per client address and per account, so password
    guessing and mail floods stop."""

    def hit(self, bucket: str, key: str) -> None:
        """Count one request from `key` against `bucket`.

        Raises RateLimitedError (core/auth/service.py) when `key` has already
        used every request `bucket` allows in its window; that request is not
        counted.
        """
        ...


class Clock(Protocol):
    """The current time, timezone-aware UTC. Injected so expiry is testable."""

    def now(self) -> datetime: ...

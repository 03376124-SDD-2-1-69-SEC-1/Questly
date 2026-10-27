"""Application composition for Questly.

This module builds repositories and services, puts them on `app.state`, includes
the routers, and mounts static files. No business rules live here.

Every repository defaults to its SQL adapter: omit an argument to `create_app`
and the application talks to Postgres, or fails at startup saying which
environment variable is missing. There is no in-memory mode — a test that wants
one passes a fake from `tests/fakes/` explicitly.
"""

import functools
import logging
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from openrouter import OpenRouter
from sqlmodel import Session
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from questly.ai.app.repository import VectorRepository
from questly.ai.app.service import VectorService
from questly.ai.client import (
    OpenRouterGenerationClient,
    StubGenerationClient,
    VectorCitationRetriever,
)
from questly.ai.embeddings.openrouter import create_openrouter_embedding_provider
from questly.ai.ingestion.document_ingestor import PdfDocumentIngestor
from questly.ai.ingestion.pdf_extractor import PyMuPDFPageExtractor
from questly.ai.ingestion.pipeline import PdfIngestionService
from questly.ai.quota import OpenRouterProviderQuota, StubProviderQuota
from questly.ai.retrieval.service import RetrievalService
from questly.api_errors import ApplicationErrorResponse
from questly.config import PRODUCTION, Settings, get_settings
from questly.core.admin.pages import router as admin_page_router
from questly.core.admin.ports import AdminRepository, SystemHealthChecker
from questly.core.admin.routes import router as admin_router
from questly.core.admin.service import AdminService
from questly.core.assignments.pages import router as assignment_page_router
from questly.core.assignments.ports import (
    AssignmentRepository,
    PostingRepository,
    PostingStats,
    VersionRepository,
)
from questly.core.assignments.routes import (
    classroom_router as assignment_classroom_router,
)
from questly.core.assignments.routes import router as assignment_router
from questly.core.assignments.service import AssignmentService
from questly.core.assignments.testcase_routes import router as test_case_router
from questly.core.auth.csrf import csrf_token
from questly.core.auth.models import (
    CsrfTokenError,
    NotAuthenticatedError,
    PermissionDeniedError,
)
from questly.core.auth.pages import DemoAccount, error_page
from questly.core.auth.pages import router as auth_page_router
from questly.core.auth.ports import AuthRepository, Clock, RateLimiter
from questly.core.auth.routes import router as auth_router
from questly.core.auth.service import AuthService, RateLimitBucket
from questly.core.classrooms.pages import router as classroom_page_router
from questly.core.classrooms.ports import ClassroomRepository, ClassroomStats
from questly.core.classrooms.routes import router as classroom_router
from questly.core.classrooms.service import ClassroomService
from questly.core.classrooms.stats import ComputedClassroomStats
from questly.core.documents.pages import router as document_page_router
from questly.core.documents.ports import (
    DocumentIngestor,
    KnowledgeDocumentRepository,
    ObjectStorage,
)
from questly.core.documents.routes import router as document_router
from questly.core.documents.service import DocumentService, fail_stuck_ingestions
from questly.core.generation.pages import router as generation_page_router
from questly.core.generation.ports import (
    DocumentCatalog,
    DraftRepository,
    GenerationClient,
    GenerationJobStore,
    ProviderQuota,
)
from questly.core.generation.routes import classroom_router as draft_classroom_router
from questly.core.generation.routes import router as draft_router
from questly.core.generation.service import GenerationService
from questly.core.notifications.pages import header_notifications
from questly.core.notifications.pages import router as notification_page_router
from questly.core.notifications.ports import EmailSender, NotificationRepository
from questly.core.notifications.routes import router as notification_router
from questly.core.notifications.service import DueSoonService, NotificationService
from questly.core.submissions.pages import router as submission_page_router
from questly.core.submissions.ports import (
    BatchRunner,
    CodeRunner,
    SubmissionRepository,
)
from questly.core.submissions.routes import (
    classroom_router as submission_classroom_router,
)
from questly.core.submissions.routes import router as submission_router
from questly.core.submissions.service import SubmissionService
from questly.core.submissions.stats import SubmissionPostingStats
from questly.core.topics.ports import TopicRepository
from questly.core.topics.routes import router as topic_router
from questly.core.topics.service import TopicService
from questly.database.core.admin_repository import SQLAdminRepository
from questly.database.core.assignment_repository import SQLAssignmentRepository
from questly.database.core.auth_repository import SQLAuthRepository
from questly.database.core.classroom_repository import SQLClassroomRepository
from questly.database.core.document_catalog_repository import SQLDocumentCatalog
from questly.database.core.draft_repository import SQLDraftRepository
from questly.database.core.knowledge_document_repository import (
    SQLKnowledgeDocumentRepository,
)
from questly.database.core.notification_repository import (
    SQLNotificationRepository,
)
from questly.database.core.posting_repository import SQLPostingRepository
from questly.database.core.submission_repository import SQLSubmissionRepository
from questly.database.core.topic_repository import SQLTopicRepository
from questly.database.core.version_repository import SQLVersionRepository
from questly.database.health import SQLSystemHealthChecker, check_db
from questly.database.rag.vector_repository import PostgresVectorRepository
from questly.database.session import (
    SessionFactory,
    build_engine,
    build_session_factory,
    get_session,
)
from questly.database.storage.r2 import R2ObjectStorage, build_r2_client, check_r2
from questly.integrations.clock import SystemClock
from questly.integrations.email import (
    QueuedEmailSender,
    ResendEmailSender,
    SmtpEmailSender,
    StubEmailSender,
)
from questly.integrations.generation_jobs import InMemoryGenerationJobStore
from questly.integrations.judge0 import StubCodeRunner, connect_judge0
from questly.integrations.rate_limit import RateLimit, SlidingWindowRateLimiter

_PACKAGE_DIR = Path(__file__).resolve().parent
_WEB_DIR = _PACKAGE_DIR / "web"
_TEMPLATE_DIR = _WEB_DIR / "templates"
_STATIC_DIR = _WEB_DIR / "static"
# The quota line is on every T-03 and T-03c load: give up on OpenRouter fast.
_QUOTA_TIMEOUT_MS = 5_000
# How often the sweeper looks for Submissions whose judging must be taken over.
_SWEEP_SECONDS = 15.0

logger = logging.getLogger("questly.main")

# Per client address: slows password guessing and caps the mail one address
# can make us send (OPS-44).
AUTH_RATE_LIMITS = {
    RateLimitBucket.LOGIN: RateLimit(limit=10, window_seconds=5 * 60),
    RateLimitBucket.SIGNUP: RateLimit(limit=5, window_seconds=60 * 60),
    RateLimitBucket.RESEND: RateLimit(limit=5, window_seconds=15 * 60),
}


DISPLAY_ZONE = ZoneInfo("Asia/Bangkok")


def local_time(value: datetime, pattern: str = "%-d %b, %H:%M") -> str:
    """Render a stored UTC time in Bangkok time for templates (`|local`)."""
    local_value = value.astimezone(DISPLAY_ZONE)
    day_placeholder = "\x00LOCAL_TIME_DAY\x00"
    portable_pattern = pattern.replace("%-d", day_placeholder)
    return local_value.strftime(portable_pattern).replace(
        day_placeholder, str(local_value.day)
    )


def asset_url(path: str) -> str:
    """Return `/static/<path>?v=<mtime>` so a rebuilt file gets a new URL.

    StaticFiles sends no Cache-Control, so browsers cache app.css heuristically
    from Last-Modified and can keep an old build for days, even across a
    reload. The version changes whenever the file on disk changes, including
    a `tailwindcss --watch` rebuild while the server keeps running.
    """
    version = int((_STATIC_DIR / path).stat().st_mtime)
    return f"/static/{path}?v={version}"


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}
# Pages run no JavaScript (AGENTS.md), use inline style attributes and load
# Noto Sans Thai from Google Fonts. A task that adds a script must widen this.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'none'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src https://fonts.gstatic.com; img-src 'self' data:; "
    "form-action 'self'; frame-ancestors 'none'; base-uri 'none'; "
    "object-src 'none'"
)
STRICT_TRANSPORT_SECURITY = "max-age=31536000"
# Swagger UI and ReDoc need their scripts, so they go without the CSP.
_DOCS_PATHS = ("/docs", "/docs/oauth2-redirect", "/redoc")


class SecurityHeadersMiddleware:
    """Add SECURITY_HEADERS to every response and the CSP to HTML pages.

    Plain ASGI, so it is the one `async` piece of the app: Starlette has no
    synchronous middleware.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        strict_transport: bool,
        paths_without_csp: tuple[str, ...],
    ) -> None:
        self._app = app
        self._strict_transport = strict_transport
        self._paths_without_csp = paths_without_csp

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        path = scope["path"]

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.update(SECURITY_HEADERS)
                if self._strict_transport:
                    headers["Strict-Transport-Security"] = STRICT_TRANSPORT_SECURITY
                is_html = headers.get("content-type", "").startswith("text/html")
                if is_html and path not in self._paths_without_csp:
                    headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
            await send(message)

        await self._app(scope, receive, send_with_headers)


class Runner(CodeRunner, BatchRunner, Protocol):
    """One runner serves Run (CodeRunner) and background Submit (BatchRunner)."""


def build_code_runner(settings: Settings) -> Runner:
    """Judge0 CE when JUDGE0_URL is set; otherwise the stub, which runs nothing."""
    if not settings.judge0_url:
        return StubCodeRunner()
    return connect_judge0(settings.judge0_url, settings.judge0_auth_token)


def _build_email_sender(settings: Settings) -> EmailSender:
    """The adapter EMAIL_BACKEND names; config.py has checked its variables."""
    if settings.email_backend == "smtp":
        return SmtpEmailSender(
            host=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password,
            sender=settings.email_from,
            security=settings.smtp_security,
        )
    if settings.email_backend == "resend":
        return ResendEmailSender(
            api_key=settings.resend_api_key, sender=settings.email_from
        )
    return StubEmailSender()


def create_app(
    *,
    settings: Settings | None = None,
    topic_repository: TopicRepository | None = None,
    assignment_repository: AssignmentRepository | None = None,
    version_repository: VersionRepository | None = None,
    posting_repository: PostingRepository | None = None,
    posting_stats: PostingStats | None = None,
    knowledge_document_repository: KnowledgeDocumentRepository | None = None,
    object_storage: ObjectStorage | None = None,
    document_ingestor: DocumentIngestor | None = None,
    generation_client: GenerationClient | None = None,
    provider_quota: ProviderQuota | None = None,
    generation_job_store: GenerationJobStore | None = None,
    draft_repository: DraftRepository | None = None,
    document_catalog: DocumentCatalog | None = None,
    vector_repository: VectorRepository | None = None,
    auth_repository: AuthRepository | None = None,
    email_sender: EmailSender | None = None,
    bulk_email_sender: EmailSender | None = None,
    notification_repository: NotificationRepository | None = None,
    admin_repository: AdminRepository | None = None,
    system_health_checker: SystemHealthChecker | None = None,
    clock: Clock | None = None,
    classroom_repository: ClassroomRepository | None = None,
    classroom_stats: ClassroomStats | None = None,
    submission_repository: SubmissionRepository | None = None,
    code_runner: Runner | None = None,
    demo_accounts: tuple[DemoAccount, ...] | None = None,
    secure_cookies: bool | None = None,
    rate_limiter: RateLimiter | None = None,
    expose_docs: bool | None = None,
) -> FastAPI:
    """Build the application, wiring SQL adapters for anything not supplied."""
    # Resolved lazily and at most once each, so an app built entirely from
    # injected adapters never reads the environment or opens a connection.
    resolved_settings: list[Settings] = []
    resolved_factory: list[SessionFactory] = []

    def use_settings() -> Settings:
        if settings is not None:
            return settings
        if not resolved_settings:
            resolved_settings.append(get_settings())
        return resolved_settings[0]

    def use_session_factory() -> SessionFactory:
        if not resolved_factory:
            resolved_factory.append(build_session_factory(build_engine(use_settings())))
        return resolved_factory[0]

    # /docs, /redoc and /openapi.json are on unless production_app() hides them.
    if expose_docs is None:
        expose_docs = True
    application = FastAPI(
        title="Questly",
        description="A modular-monolith service for instructor assignment authoring.",
        version="0.1.0",
        responses={422: {"model": ApplicationErrorResponse}},
        docs_url="/docs" if expose_docs else None,
        redoc_url="/redoc" if expose_docs else None,
        openapi_url="/openapi.json" if expose_docs else None,
    )
    application.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

    templates = Jinja2Templates(directory=_TEMPLATE_DIR)
    templates.env.globals["asset_url"] = asset_url
    templates.env.globals["csrf_token"] = csrf_token
    templates.env.globals["header_notifications"] = header_notifications
    templates.env.filters["local"] = local_time
    application.state.templates = templates
    # The "log in as" list on G-01 exists only when scripts/demo.py passes it.
    if demo_accounts is None:
        demo_accounts = ()
    application.state.demo_accounts = demo_accounts
    # Cookies are Secure unless a caller on plain HTTP (scripts/demo.py, the
    # tests) turns it off explicitly.
    if secure_cookies is None:
        secure_cookies = True
    application.state.secure_cookies = secure_cookies
    # HSTS only where cookies are Secure, i.e. where the app is served on HTTPS.
    application.add_middleware(
        SecurityHeadersMiddleware,
        strict_transport=secure_cookies,
        paths_without_csp=_DOCS_PATHS if expose_docs else (),
    )
    if rate_limiter is None:
        rate_limiter = SlidingWindowRateLimiter(AUTH_RATE_LIMITS)
    application.state.rate_limiter = rate_limiter

    if clock is None:
        clock = SystemClock()

    # Every classroom slice persists to Postgres (OPS-15); a caller may inject
    # any adapter instead, as the tests and scripts/demo.py do. Repositories
    # are resolved before the services because ClassroomStats reads four of
    # them.
    if auth_repository is None:
        auth_repository = SQLAuthRepository(use_session_factory())
    if classroom_repository is None:
        classroom_repository = SQLClassroomRepository(use_session_factory())
    if assignment_repository is None:
        assignment_repository = SQLAssignmentRepository(use_session_factory())
    if version_repository is None:
        version_repository = SQLVersionRepository(use_session_factory())
    if posting_repository is None:
        posting_repository = SQLPostingRepository(use_session_factory())
    if submission_repository is None:
        submission_repository = SQLSubmissionRepository(use_session_factory())
    if draft_repository is None:
        draft_repository = SQLDraftRepository(use_session_factory())
    if document_catalog is None:
        document_catalog = SQLDocumentCatalog(use_session_factory())
    if classroom_stats is None:
        classroom_stats = ComputedClassroomStats(
            posting_repository,
            submission_repository,
            classroom_repository,
            draft_repository,
            clock,
        )

    if email_sender is None:
        email_sender = _build_email_sender(use_settings())

    if notification_repository is None:
        notification_repository = SQLNotificationRepository(use_session_factory())

    notification_service = NotificationService(
        notification_repository,
        email_sender,
        clock,
        classroom_repository,
        auth_repository,
        base_url=use_settings().base_url,
        # None sends member emails through email_sender, on the request.
        bulk_email_sender=bulk_email_sender,
    )
    application.state.notification_service = notification_service

    if admin_repository is None:
        admin_repository = SQLAdminRepository(use_session_factory())
    # Resolved before the health checker, which reports whether it is a stub.
    if code_runner is None:
        code_runner = build_code_runner(use_settings())
    if system_health_checker is None:
        system_health_checker = SQLSystemHealthChecker(
            use_session_factory(),
            generation_available=(
                bool(use_settings().openrouter_api_key)
                or (
                    generation_client is not None
                    and not isinstance(generation_client, StubGenerationClient)
                )
            ),
            sandbox_available=not isinstance(code_runner, StubCodeRunner),
            email_available=(not isinstance(email_sender, StubEmailSender)),
        )
    if provider_quota is None:
        if use_settings().openrouter_api_key:
            provider_quota = OpenRouterProviderQuota(
                OpenRouter(
                    api_key=use_settings().openrouter_api_key,
                    timeout_ms=_QUOTA_TIMEOUT_MS,
                ),
                model=lambda: admin_repository.get_ai_settings().model,
                generations_since=draft_repository.count_all_generations,
                clock=clock,
            )
        else:
            provider_quota = StubProviderQuota(clock)
    auth_service = AuthService(
        auth_repository,
        notification_service,
        clock,
        base_url=use_settings().base_url,
        # The stub sends nothing, so development shows the link on G-04a.
        show_demo_link=(
            isinstance(email_sender, StubEmailSender)
            and use_settings().app_env != PRODUCTION
        ),
        onscreen_verify_domains=use_settings().onscreen_verify_domains,
    )
    application.state.auth_service = auth_service
    admin_service = AdminService(
        admin_repository,
        auth_repository,
        system_health_checker,
        generation_usage=draft_repository,
        provider_quota=provider_quota,
        clock=clock,
        allowed_models=use_settings().generation_model_allowlist,
    )
    application.state.admin_service = admin_service

    def get_daily_quota() -> int:
        return admin_repository.get_ai_settings().daily_quota

    classroom_service = ClassroomService(
        classroom_repository, auth_service, classroom_stats, clock
    )
    application.state.classroom_service = classroom_service

    # T-01/S-01 numbers are computed from Submissions unless a test injects its
    # own PostingStats.
    if posting_stats is None:
        posting_stats = SubmissionPostingStats(
            submission_repository, assignment_repository
        )
    assignment_service = AssignmentService(
        assignment_repository,
        version_repository,
        posting_repository,
        classroom_service,
        posting_stats,
        clock,
        notification_service,
    )
    application.state.assignment_service = assignment_service
    application.state.due_soon_service = DueSoonService(
        classroom_service, assignment_service, clock
    )

    application.state.submission_service = SubmissionService(
        submission_repository,
        code_runner,
        code_runner,
        assignment_service,
        classroom_service,
        version_repository,
        clock,
    )

    if vector_repository is None:
        vector_repository = PostgresVectorRepository(use_session_factory())
    vector_service = VectorService(vector_repository)
    application.state.vector_service = vector_service

    if generation_client is None:
        if use_settings().openrouter_api_key:
            embedding_provider = create_openrouter_embedding_provider(
                use_settings().openrouter_api_key,
                use_settings().embedding_model,
            )
            generation_client = OpenRouterGenerationClient(
                api_key=use_settings().openrouter_api_key,
                model=lambda: admin_repository.get_ai_settings().model,
                code_runner=code_runner,
                retriever=VectorCitationRetriever(
                    embedding_provider, RetrievalService(vector_repository)
                ),
            )
        else:
            generation_client = StubGenerationClient()
    if generation_job_store is None:
        generation_job_store = InMemoryGenerationJobStore()
    application.state.generation_service = GenerationService(
        draft_repository,
        generation_client,
        document_catalog,
        classroom_service,
        assignment_service,
        clock,
        daily_quota=get_daily_quota,
        jobs=generation_job_store,
        provider_quota=provider_quota,
    )

    if topic_repository is None:
        topic_repository = SQLTopicRepository(use_session_factory())
    application.state.topic_service = TopicService(topic_repository)

    if object_storage is None:
        storage_settings = use_settings()
        object_storage = R2ObjectStorage(
            build_r2_client(storage_settings), storage_settings.r2_bucket_name
        )
    if knowledge_document_repository is None:
        knowledge_document_repository = SQLKnowledgeDocumentRepository(
            use_session_factory()
        )
    # Without an OpenRouter key nothing is ingested and uploads stay `uploaded`.
    if document_ingestor is None and use_settings().openrouter_api_key:
        document_ingestor = PdfDocumentIngestor(
            PdfIngestionService(
                PyMuPDFPageExtractor(),
                create_openrouter_embedding_provider(
                    use_settings().openrouter_api_key,
                    use_settings().embedding_model,
                ),
                vector_service,
            ),
            vector_service,
        )
    application.state.document_service = DocumentService(
        object_storage,
        knowledge_document_repository,
        max_upload_size_bytes=use_settings().max_upload_size_bytes,
        ingestor=document_ingestor,
    )
    application.state.fail_stuck_ingestions = functools.partial(
        fail_stuck_ingestions, knowledge_document_repository
    )

    application.include_router(topic_router)
    application.include_router(assignment_router)
    application.include_router(assignment_classroom_router)
    application.include_router(test_case_router)
    application.include_router(assignment_page_router)
    application.include_router(draft_router)
    application.include_router(draft_classroom_router)
    application.include_router(generation_page_router)
    application.include_router(document_page_router)
    application.include_router(document_router)
    application.include_router(notification_router)
    application.include_router(notification_page_router)
    application.include_router(auth_router)
    application.include_router(auth_page_router)
    application.include_router(admin_router)
    application.include_router(admin_page_router)
    application.include_router(classroom_router)
    application.include_router(classroom_page_router)
    application.include_router(submission_router)
    application.include_router(submission_classroom_router)
    application.include_router(submission_page_router)

    @application.exception_handler(NotAuthenticatedError)
    def not_authenticated(request: Request, error: NotAuthenticatedError) -> Response:
        """API callers get 401; a browser on a page is sent to /login."""
        if request.url.path.startswith("/api/"):
            return JSONResponse(
                status_code=401,
                content={
                    "detail": {"code": "not_authenticated", "message": "Log in first"}
                },
            )
        return RedirectResponse("/login", status_code=303)

    @application.exception_handler(PermissionDeniedError)
    def permission_denied(request: Request, error: PermissionDeniedError) -> Response:
        """A member in the wrong role (ADR-0007 §9.6)."""
        if request.url.path.startswith("/api/"):
            return JSONResponse(
                status_code=403,
                content={"detail": {"code": "forbidden", "message": "Not allowed"}},
            )
        return error_page(request, 403)

    @application.exception_handler(CsrfTokenError)
    def csrf_rejected(request: Request, error: CsrfTokenError) -> Response:
        """A form posted without its page's token (core/auth/csrf.py)."""
        return error_page(
            request,
            403,
            title="ฟอร์มนี้หมดอายุแล้ว",
            message="กลับไปที่หน้าเดิม โหลดหน้าใหม่ แล้วลองส่งอีกครั้ง",
        )

    @application.exception_handler(StarletteHTTPException)
    def http_error(request: Request, error: StarletteHTTPException) -> Response:
        """Browsers get G-05; `/api/`, `/health` and `/static` keep the JSON body.

        Both keep the error's headers, such as Retry-After on a 429.
        """
        if request.url.path.startswith(("/api/", "/health", "/static/")):
            return JSONResponse(
                status_code=error.status_code,
                content={"detail": error.detail},
                headers=error.headers,
            )
        page = error_page(request, error.status_code)
        if error.headers is not None:
            page.headers.update(error.headers)
        return page

    @application.exception_handler(Exception)
    def server_error(request: Request, error: Exception) -> Response:
        """An unhandled bug: a 500 page for browsers, the usual envelope for the API."""
        if request.url.path.startswith(("/api/", "/health")):
            return JSONResponse(
                status_code=500,
                content={
                    "detail": {"code": "internal_error", "message": "Server error"}
                },
            )
        return error_page(request, 500)

    @application.exception_handler(RequestValidationError)
    def request_validation_error(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        """Use the application error envelope without echoing submitted values."""
        return JSONResponse(
            status_code=422,
            content={
                "detail": {
                    "code": "request_validation_error",
                    "message": "Request body or parameters are invalid",
                }
            },
        )

    @application.get("/health")
    def health() -> dict[str, str]:
        """Report application liveness without database dependencies."""
        return {"status": "ok", "service": "questly"}

    @application.get("/health/db")
    def health_db(
        session: Session = Depends(get_session),  # noqa: B008 — FastAPI DI
    ) -> dict:
        """Confirm the Neon connection works and core/rag schemas exist."""
        try:
            return check_db(session)
        except Exception as exc:  # noqa: BLE001 — surface as a 503, not a 500
            # The error can name the host or the user; it goes to the log only.
            logger.exception("database health check failed")
            raise HTTPException(status_code=503, detail="database unavailable") from exc

    @application.get("/health/r2")
    def health_r2() -> dict:
        """Confirm the R2 bucket is reachable."""
        bucket_settings = use_settings()
        try:
            return check_r2(
                build_r2_client(bucket_settings), bucket_settings.r2_bucket_name
            )
        except Exception as exc:  # noqa: BLE001 — surface as a 503, not a 500
            logger.exception("storage health check failed")
            raise HTTPException(status_code=503, detail="storage unavailable") from exc

    return application


def run_sweeper(
    recover: Callable[[], None], stop: threading.Event, period_seconds: float
) -> None:
    """Call `recover` every `period_seconds` until `stop` is set.

    A failing sweep is logged and the next one runs anyway, so one bad row
    never ends recovery for the rest.
    """
    while not stop.is_set():
        try:
            recover()
        except Exception:
            logger.exception("judging sweep failed")
        stop.wait(period_seconds)


def start_sweeper(recover: Callable[[], None]) -> None:
    """Run the sweeper on a daemon thread for the life of the process."""
    threading.Thread(
        target=run_sweeper,
        args=(recover, threading.Event(), _SWEEP_SECONDS),
        name="judging-sweeper",
        daemon=True,
    ).start()


@functools.cache
def production_app() -> FastAPI:
    """The served app with its judging sweeper, member-email queue and recovery.

    The sweeper, the queue and the ingestion recovery start here and never in
    `create_app`, so tests build apps without threads or startup writes; a
    lifespan hook would need `async def`, which this codebase does not use.
    Verification emails stay on the request, so G-04a can say whether one went
    out.
    """
    settings = get_settings()
    email_sender = _build_email_sender(settings)
    member_emails = QueuedEmailSender(email_sender)
    application = create_app(
        settings=settings,
        expose_docs=settings.app_env != PRODUCTION,
        email_sender=email_sender,
        bulk_email_sender=member_emails,
    )
    member_emails.start()
    try:
        application.state.fail_stuck_ingestions()
    except Exception:
        logger.exception("could not fail documents left ingesting")
    start_sweeper(application.state.submission_service.recover)
    return application


def __getattr__(name: str) -> FastAPI:
    """Build the ASGI app the first time `questly.main:app` is resolved.

    `uvicorn questly.main:app` and `fastapi dev` both need a module attribute,
    but building it at import time would make importing this module — which every
    test does — require a populated `.env`.
    """
    if name == "app":
        return production_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

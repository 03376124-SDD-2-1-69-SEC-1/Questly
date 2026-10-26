"""Executable versions of the rules in AGENTS.md and docs/task-scope.md.

Each test here exists because a prose rule was broken by someone who never saw it.
When you add a rule to AGENTS.md, add its test here in the same PR.
"""

import ast
import re
import subprocess
from pathlib import Path

CORE_ROOT = Path("src/questly/core")
SRC_ROOT = Path("src/questly")
MAIN_PY = Path("src/questly/main.py")
TESTS_ROOT = Path("tests")
LEGACY_ASSIGNMENTS_TEST_DIR = TESTS_ROOT / "unit" / "core" / "topics" / "assignments"


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _is_pytest_filename(path: Path) -> bool:
    return path.name.startswith("test_") or path.name.endswith("_test.py")


def test_no_any_under_core() -> None:
    violations = []
    for source_file in sorted(CORE_ROOT.rglob("*.py")):
        tree = _parse(source_file)
        for node in ast.walk(tree):
            is_any_name = isinstance(node, ast.Name) and node.id == "Any"
            is_any_import = (
                isinstance(node, ast.ImportFrom)
                and node.module == "typing"
                and any(alias.name == "Any" for alias in node.names)
            )
            if is_any_name or is_any_import:
                violations.append(f"{source_file}:{node.lineno}")

    assert not violations, (
        "`Any` is banned under core/ (AGENTS.md 'Typing rules' -> 'No Any'). "
        "Replace it with a concrete type or a domain dataclass. Violations:\n"
        + "\n".join(violations)
    )


def _is_bare_dict_annotation(node: ast.expr) -> bool:
    if isinstance(node, ast.Name):
        return node.id in ("dict", "Dict")
    if isinstance(node, ast.Subscript):
        value = node.value
        if isinstance(value, ast.Name):
            return value.id in ("dict", "Dict")
        if isinstance(value, ast.Attribute):
            return value.attr in ("dict", "Dict")
    return False


def test_service_and_route_functions_do_not_return_bare_dict() -> None:
    violations = []
    for filename in ("service.py", "routes.py"):
        for source_file in sorted(CORE_ROOT.glob(f"*/{filename}")):
            tree = _parse(source_file)
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.FunctionDef)
                    and node.returns is not None
                    and _is_bare_dict_annotation(node.returns)
                ):
                    violations.append(f"{source_file}:{node.lineno}: {node.name}")

    assert not violations, (
        "A service or route must return a domain object, not a dict "
        "(AGENTS.md 'Typing rules' -> 'No Any': 'A service or repository must "
        "never return a bare dict from a public method'). Violations:\n"
        + "\n".join(violations)
    )


def test_no_pytest_files_under_src() -> None:
    matches = sorted(p for p in SRC_ROOT.rglob("*.py") if _is_pytest_filename(p))

    assert not matches, (
        "Test files belong under tests/, not src/questly/. Move these:\n"
        + "\n".join(str(p) for p in matches)
    )


def test_pytest_filename_check_ignores_the_test_cases_slice_name() -> None:
    """`core/test_cases/` is a domain slice (Assignment test cases), not a
    tests directory. The src-test-file check above matches filenames, not
    directory names, so it must not flag `test_cases/models.py`."""
    assert not _is_pytest_filename(Path("src/questly/core/test_cases/models.py"))
    assert _is_pytest_filename(Path("src/questly/core/test_cases/test_foo.py"))
    assert _is_pytest_filename(Path("src/questly/core/test_cases/foo_test.py"))


def _is_include_router_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "include_router"
    )


def test_routers_are_mounted_only_inside_create_app() -> None:
    tree = _parse(MAIN_PY)
    create_app_node = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "create_app"
        ),
        None,
    )
    assert create_app_node is not None, f"{MAIN_PY} must define create_app()"

    inside_create_app = {id(node) for node in ast.walk(create_app_node)}

    violations = [
        f"{MAIN_PY}:{node.lineno}"
        for node in ast.walk(tree)
        if _is_include_router_call(node) and id(node) not in inside_create_app
    ]

    assert not violations, (
        "include_router(...) must be called only inside create_app() -- a "
        "module-scope call after `app = create_app()` skips the per-test "
        "create_app() fixture and mounts the router on the shared singleton "
        "only. Violations:\n" + "\n".join(violations)
    )


def test_no_files_under_legacy_assignments_test_path() -> None:
    matches = (
        sorted(LEGACY_ASSIGNMENTS_TEST_DIR.rglob("*.py"))
        if LEGACY_ASSIGNMENTS_TEST_DIR.exists()
        else []
    )

    assert not matches, (
        "tests/unit/core/topics/assignments/ nests Assignment tests under the "
        "Topics slice by accident. Move HTTP tests to tests/integration/ and "
        "unit tests to tests/unit/core/assignments/. Found:\n"
        + "\n".join(str(p) for p in matches)
    )


def _imports_http_test_client(tree: ast.Module) -> bool:
    return any(
        isinstance(node, ast.ImportFrom)
        and any(alias.name in ("AsyncClient", "TestClient") for alias in node.names)
        for node in ast.walk(tree)
    )


# A field goes in either allowlist only when a use case reads it and the PR
# says which one. Expected first entry for ALLOWED_TIMESTAMP_FIELDS:
# `approved_at` on a draft, once Questly's approval flow needs it -- unlike
# `created_at`/`updated_at`, that's a business fact, not row bookkeeping.
ALLOWED_TIMESTAMP_FIELDS: set[str] = {
    # notifications: list timestamps and determine whether a notification was read.
    "created_at",
    "read_at",
    # auth (ADR-0007 §1): verify_email refuses a link after 24 h and
    # resolve_session refuses an expired session.
    "expires_at",
    # auth: resolve_session writes it at most hourly; A-01 "Last active".
    "last_active_at",
    # auth: A-01 pending Instructor requests, "Requested" column.
    "requested_at",
    # auth (ADR-0007 §8): A-01 records when an Instructor request was reviewed.
    "reviewed_at",
    # classrooms: T-01 Members "Joined" column and its ordering.
    "joined_at",
    # assignments: Posting.is_closed reads closed_at; published_at orders the
    # problem numbers on T-01/S-01; changed_at is T-02b's "Changed" column.
    "closed_at",
    "published_at",
    "changed_at",
    # generation: T-01 Drafts "Created" column and T-03a "Generated ...".
    "generated_at",
    # generation (ADR-0010): T-03c shows elapsed and per-step seconds, and
    # GenerationJob.is_stale (3 minutes) reads started_at.
    "started_at",
    "step_started_at",
    "finished_at",
    # generation (ADR-0010): T-03's "resets 07:00" line.
    "resets_at",
    # submissions (ADR-0007 §5.6): is_late compares submitted_at with the
    # deadline; S-02g and T-02 show it in "Submitted".
    "submitted_at",
}
# `chunk_id`/`source_id` on generation/models.py::Citation point at rows in the
# `rag` schema. There is no FK -- core and rag never reference each other by key -- so a
# Citation carries them as a business fact about itself (which source chunk it
# quotes), not as a column that exists only to satisfy a table (OPS-12).
ALLOWED_ID_SUFFIX_FIELDS = {
    "artifact_id",
    "chunk_id",
    "source_id",
    # auth (ADR-0007 §9.5): whose account a Session, token or Instructor
    # request is, and who an Actor is -- every authorization check reads it.
    "user_id",
    # classrooms (ADR-0007 §9.6): ClassroomService._owned compares
    # instructor_id with the Actor, and _visible looks up a Membership by
    # classroom_id + student_id -- the 404/403 rule reads all three.
    "instructor_id",
    "classroom_id",
    "student_id",
    # assignments: AssignmentService._owned_assignment compares owner_id with
    # the Actor; Versions and Postings are listed by assignment_id; topic_id
    # is the one Topic T-03/T-04 pick (ADR-0007 §3.5).
    "owner_id",
    "assignment_id",
    # submissions: Submissions are listed by posting_id (S-02g history, T-02
    # results, PostingStats) -- the Counted Submission is found through it.
    "posting_id",
    "topic_id",
    # generation (ADR-0010): a done job points T-03c at the Draft to open.
    "draft_id",
}


def _dataclass_field_names(tree: ast.Module) -> list[tuple[str, str, int]]:
    fields = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for item in node.body:
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                fields.append((node.name, item.target.id, item.lineno))
    return fields


def test_domain_models_hold_no_table_only_fields() -> None:
    violations = []
    for source_file in sorted(CORE_ROOT.glob("*/models.py")):
        tree = _parse(source_file)
        for class_name, field_name, lineno in _dataclass_field_names(tree):
            if (
                field_name.endswith("_at")
                and field_name not in ALLOWED_TIMESTAMP_FIELDS
            ):
                violations.append(
                    f"{source_file}:{lineno}: {class_name}.{field_name} "
                    "(looks like a persistence timestamp)"
                )
            elif (
                field_name.endswith("_id")
                and field_name not in ALLOWED_ID_SUFFIX_FIELDS
            ):
                violations.append(
                    f"{source_file}:{lineno}: {class_name}.{field_name} "
                    "(looks like a foreign-key column)"
                )

    assert not violations, (
        "AGENTS.md 'Layering' -> models.py: the domain layer holds business "
        "concepts only and does not carry fields that exist to satisfy a "
        "table (see CORE-11). A field ending in `_at` or `_id` must be "
        "allowlisted in ALLOWED_TIMESTAMP_FIELDS / ALLOWED_ID_SUFFIX_FIELDS "
        "if a use case reads it -- say which one in the PR. Violations:\n"
        + "\n".join(violations)
    )


# A process-memory adapter that production deliberately wires. Each entry needs
# an ADR saying why; anything not listed here is still an accident.
ALLOWED_IN_MEMORY_ADAPTERS = {
    # ADR-0010: Generate jobs live in this process for the PoC (one user, one
    # process). The GenerationJobStore port lets a database store replace it.
    "InMemoryGenerationJobStore",
}


def test_no_in_memory_adapter_ships_inside_src() -> None:
    """Fakes live in tests/fakes/, never in the package.

    An in-memory adapter inside `src/` can be wired into a running application by
    accident, which is how every endpoint came to serve process memory while the
    Neon database sat unused. Keeping them out of the package makes that
    impossible rather than merely discouraged.
    """
    violations = []
    for source_file in sorted(SRC_ROOT.rglob("*.py")):
        tree = _parse(source_file)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if (
                node.name.startswith(("InMemory", "Fake"))
                and node.name not in ALLOWED_IN_MEMORY_ADAPTERS
            ):
                violations.append(f"{source_file}:{node.lineno}: {node.name}")

    assert not violations, (
        "In-memory adapters belong in tests/fakes/, not in src/questly/. "
        "Violations:\n" + "\n".join(violations)
    )


def test_create_app_has_no_adapter_defaults() -> None:
    """Omitting an argument must mean "build the real adapter", never a fake.

    Every keyword-only argument defaults to None so `create_app()` wires SQL and
    R2 adapters; a non-None default would let the application start on something
    that is not the database.
    """
    tree = _parse(MAIN_PY)
    create_app_node = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "create_app"
        ),
        None,
    )
    assert create_app_node is not None, f"{MAIN_PY} must define create_app()"

    violations = [
        argument.arg
        for argument, default in zip(
            create_app_node.args.kwonlyargs,
            create_app_node.args.kw_defaults,
            strict=True,
        )
        if not (isinstance(default, ast.Constant) and default.value is None)
    ]

    assert not violations, (
        "create_app keyword arguments must default to None so an omitted "
        "argument builds the real adapter. Violations: " + ", ".join(violations)
    )


def test_http_client_tests_live_under_integration() -> None:
    violations = []
    for source_file in sorted(TESTS_ROOT.rglob("*.py")):
        if TESTS_ROOT / "integration" in source_file.parents:
            continue
        if _imports_http_test_client(_parse(source_file)):
            violations.append(str(source_file))

    assert not violations, (
        "Files that use AsyncClient or TestClient are HTTP tests and belong "
        "under tests/integration/, matching "
        "tests/integration/test_topics_api.py. Violations:\n" + "\n".join(violations)
    )


# --- ADR-0007: classroom-centric flow ------------------------------------------

TEMPLATE_ROOT = SRC_ROOT / "web" / "templates"
# The one template outside a page group: the layout every page extends.
TOP_LEVEL_TEMPLATES = {"base.html"}
# Page group directory -> the page-ID letters it may hold (G/C shared, S, T, A).
TEMPLATE_GROUPS = {
    "shared": ("g", "c"),
    "student": ("s",),
    "instructor": ("t",),
    "admin": ("a",),
}
TEMPLATE_NAME = re.compile(r"^([a-z])\d{2}[a-z]?_[a-z0-9_]+\.html$")
# Jinja macros every page imports (field, button, tabs, badge, ...).
COMPONENTS_DIR = "_components"
COMPONENT_NAME = re.compile(r"^[a-z][a-z0-9_]*\.html$")
RAW_CONTROL = re.compile(r"<(button|input|select|textarea)\b", re.IGNORECASE)
# Files outside core/<slice>/pages.py allowed to render templates. Empty since
# `/` became a redirect in core/auth/pages.py; keep it that way.
LEGACY_TEMPLATE_RENDERERS: set[Path] = set()
# Slices whose every public use case takes the Actor first. A slice listed here
# is checked as soon as its service.py exists; assignments and generation join
# when their use cases gain an actor.
ACTOR_SLICES = (
    "classrooms",
    "assignments",
    "generation",
    "submissions",
    "admin",
    "documents",
)
ROLE_ATTRIBUTES = {"role", "instructor_id"}
# Background judging of a Submission `submit` already authorized: only the
# BackgroundTask a Submit schedules and the sweeper call these (ADR-0011).
WORKER_ENTRY_POINTS = {("SubmissionService", "judge"), ("SubmissionService", "recover")}


def _api_modules() -> list[Path]:
    return sorted(
        path
        for path in CORE_ROOT.glob("*/*.py")
        if path.name == "routes.py" or path.name.endswith("_routes.py")
    )


def _page_modules() -> list[Path]:
    return sorted(CORE_ROOT.glob("*/pages.py"))


def _router_prefixes(path: Path) -> list[tuple[int, str]]:
    prefixes = []
    for node in ast.walk(_parse(path)):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "APIRouter"
        ):
            continue
        prefix = ""
        for keyword in node.keywords:
            if keyword.arg == "prefix" and isinstance(keyword.value, ast.Constant):
                prefix = str(keyword.value.value)
        prefixes.append((node.lineno, prefix))
    return prefixes


def test_api_routers_live_under_api_v1() -> None:
    violations = [
        f"{path}:{lineno}: prefix={prefix!r}"
        for path in _api_modules()
        for lineno, prefix in _router_prefixes(path)
        if not prefix.startswith("/api/v1/")
    ]

    assert not violations, (
        "JSON routers live under /api/v1/ (AGENTS.md 'Definition of done'). "
        "Violations:\n" + "\n".join(violations)
    )


def test_page_routers_carry_no_api_prefix() -> None:
    violations = [
        f"{path}:{lineno}: prefix={prefix!r}"
        for path in _page_modules()
        for lineno, prefix in _router_prefixes(path)
        if prefix.startswith("/api")
    ]

    assert not violations, (
        "pages.py serves HTML at page URLs, never under /api (ADR-0007 §9). "
        "Violations:\n" + "\n".join(violations)
    )


def test_only_page_modules_render_templates() -> None:
    allowed = set(_page_modules()) | LEGACY_TEMPLATE_RENDERERS
    violations = []
    for source_file in sorted(SRC_ROOT.rglob("*.py")):
        if source_file in allowed:
            continue
        for node in ast.walk(_parse(source_file)):
            if isinstance(node, ast.Attribute) and node.attr == "TemplateResponse":
                violations.append(f"{source_file}:{node.lineno}")

    assert not violations, (
        "Only core/<slice>/pages.py renders templates; routes.py returns JSON "
        "(AGENTS.md 'Definition of done'). Violations:\n" + "\n".join(violations)
    )


def test_templates_live_in_a_page_group() -> None:
    violations = []
    for template in sorted(TEMPLATE_ROOT.rglob("*.html")):
        relative = template.relative_to(TEMPLATE_ROOT)
        if relative.parts[0] == COMPONENTS_DIR:
            if not COMPONENT_NAME.match(relative.name) or len(relative.parts) != 2:
                violations.append(
                    f"{relative}: component files are _components/<name>.html"
                )
            continue
        if len(relative.parts) == 1:
            if relative.name not in TOP_LEVEL_TEMPLATES:
                violations.append(f"{relative}: not in a page group directory")
            continue
        group = relative.parts[0]
        match = TEMPLATE_NAME.match(relative.name)
        if len(relative.parts) != 2 or group not in TEMPLATE_GROUPS:
            violations.append(f"{relative}: group must be one of {TEMPLATE_GROUPS}")
        elif match is None:
            violations.append(f"{relative}: name must be <page-id>_<slug>.html")
        elif match.group(1) not in TEMPLATE_GROUPS[group]:
            violations.append(f"{relative}: page ID does not belong in {group}/")

    assert not violations, (
        "Templates live in web/templates/<group>/<page-id>_<slug>.html, e.g. "
        "student/s02_solve.html (AGENTS.md 'Definition of done'). Violations:\n"
        + "\n".join(violations)
    )


def _decision_nodes(tree: ast.Module) -> list[ast.AST]:
    """Expressions a handler branches on: comparisons and conditions."""
    nodes: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            nodes.append(node)
        elif isinstance(node, (ast.If, ast.IfExp, ast.While, ast.Assert)):
            nodes.append(node.test)
        elif isinstance(node, ast.Match):
            nodes.append(node.subject)
        elif isinstance(node, ast.comprehension):
            nodes.extend(node.ifs)
    return nodes


def test_routes_and_pages_never_branch_on_a_role() -> None:
    """Serializing a role is fine; deciding on one belongs to the service."""
    violations = []
    for source_file in _api_modules() + _page_modules():
        for decision in _decision_nodes(_parse(source_file)):
            for node in ast.walk(decision):
                if isinstance(node, ast.Attribute) and node.attr in ROLE_ATTRIBUTES:
                    violations.append(f"{source_file}:{node.lineno}: .{node.attr}")

    assert not violations, (
        "Authorization lives in the service; routes and pages never branch on "
        "a role (AGENTS.md 'Definition of done'). Violations:\n" + "\n".join(violations)
    )


def test_core_never_uses_depends() -> None:
    violations = []
    for source_file in sorted(CORE_ROOT.rglob("*.py")):
        for node in ast.walk(_parse(source_file)):
            is_name = isinstance(node, ast.Name) and node.id == "Depends"
            is_attribute = isinstance(node, ast.Attribute) and node.attr == "Depends"
            if is_name or is_attribute:
                violations.append(f"{source_file}:{node.lineno}")

    assert not violations, (
        "Services come off request.app.state and the actor from "
        "current_actor(request), never Depends() (AGENTS.md 'Stack'). "
        "Violations:\n" + "\n".join(violations)
    )


def test_classroom_use_cases_take_the_actor_first() -> None:
    violations = []
    for slice_name in ACTOR_SLICES:
        service_file = CORE_ROOT / slice_name / "service.py"
        if not service_file.exists():
            continue
        for node in ast.walk(_parse(service_file)):
            if not isinstance(node, ast.ClassDef):
                continue
            for method in node.body:
                if not isinstance(method, ast.FunctionDef):
                    continue
                if method.name.startswith("_"):
                    continue
                if (node.name, method.name) in WORKER_ENTRY_POINTS:
                    continue
                parameters = [argument.arg for argument in method.args.args]
                if parameters[1:2] != ["actor"]:
                    violations.append(
                        f"{service_file}:{method.lineno}: {node.name}.{method.name}"
                    )

    assert not violations, (
        "Every public use case in a classroom slice takes `actor` right after "
        "self (AGENTS.md 'Definition of done'). Violations:\n" + "\n".join(violations)
    )


def test_templates_build_controls_only_through_component_macros() -> None:
    """Buttons and inputs come from _components/forms.html, so they look alike."""
    violations = []
    for template in sorted(TEMPLATE_ROOT.rglob("*.html")):
        relative = template.relative_to(TEMPLATE_ROOT)
        if relative.parts[0] == COMPONENTS_DIR:
            continue
        for lineno, line in enumerate(
            template.read_text(encoding="utf-8").splitlines(), start=1
        ):
            match = RAW_CONTROL.search(line)
            if match:
                violations.append(f"{relative}:{lineno}: <{match.group(1)}>")

    assert not violations, (
        "Use the macros in web/templates/_components/forms.html (field, button, "
        "hidden, ...) instead of raw form controls (AGENTS.md 'Definition of "
        "done'). Violations:\n" + "\n".join(violations)
    )


POST_FORM = re.compile(r"<form\b[^>]*method=\"post\"[^>]*>(.*?)</form>", re.S | re.I)
FORMS_IMPORT = re.compile(
    r'\{%\s*from\s+"_components/forms\.html"\s+import\s+([^%]*?)%\}'
)


def test_every_post_form_carries_the_csrf_field() -> None:
    """core/auth/csrf.py rejects a form post without its page's token."""
    violations = []
    for template in sorted(TEMPLATE_ROOT.rglob("*.html")):
        template_text = template.read_text(encoding="utf-8")
        for match in POST_FORM.finditer(template_text):
            if "csrf_field()" not in match.group(1):
                line = template_text[: match.start()].count("\n") + 1
                violations.append(f"{template.relative_to(TEMPLATE_ROOT)}:{line}")

    assert not violations, (
        'Put {{ csrf_field() }} inside every <form method="post"> (or use '
        "action_form). Violations:\n" + "\n".join(violations)
    )


def test_forms_macros_are_imported_with_context() -> None:
    """csrf_field() reads `request`, which a context-free import cannot see."""
    violations = []
    for template in sorted(TEMPLATE_ROOT.rglob("*.html")):
        for match in FORMS_IMPORT.finditer(template.read_text(encoding="utf-8")):
            if not match.group(1).rstrip().endswith("with context"):
                violations.append(str(template.relative_to(TEMPLATE_ROOT)))

    assert not violations, (
        "Import _components/forms.html macros `with context`. Violations:\n"
        + "\n".join(violations)
    )


def _is_post_route(decorator: ast.expr) -> bool:
    return (
        isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and decorator.func.attr == "post"
    )


def test_every_post_page_handler_checks_csrf_first() -> None:
    violations = []
    for page_module in _page_modules():
        for node in ast.walk(_parse(page_module)):
            if not isinstance(node, ast.FunctionDef):
                continue
            if not any(_is_post_route(d) for d in node.decorator_list):
                continue
            parameters = {argument.arg for argument in node.args.args}
            body = [
                statement
                for statement in node.body
                if not (
                    isinstance(statement, ast.Expr)
                    and isinstance(statement.value, ast.Constant)
                )
            ]
            first = body[0] if body else None
            calls_check = (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Call)
                and isinstance(first.value.func, ast.Name)
                and first.value.func.id == "require_csrf"
            )
            if "csrf_token" not in parameters or not calls_check:
                violations.append(f"{page_module}:{node.lineno}: {node.name}")

    assert not violations, (
        "Every POST page handler takes `csrf_token` and calls "
        "require_csrf(request, csrf_token) as its first statement. "
        "Violations:\n" + "\n".join(violations)
    )


OLD_PROJECT_NAME = re.compile(r"greader(?!-ci)", re.IGNORECASE)
OLD_NAME_EXEMPT_PREFIXES = ("docs/handoff/",)
OLD_NAME_EXEMPT_FILES = {"tests/architecture/test_conventions.py"}


def test_old_project_name_is_gone() -> None:
    """A branch cut before the Questly rename re-adds the old project name.

    `greader-ci` is the real name of the CI R2 bucket and is allowed. Dated
    handoff docs are snapshots and are left alone.
    """
    tracked = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, check=True
    ).stdout.splitlines()

    violations = []
    for name in tracked:
        if name in OLD_NAME_EXEMPT_FILES or name.startswith(OLD_NAME_EXEMPT_PREFIXES):
            continue
        path = Path(name)
        if not path.is_file():
            continue
        if OLD_PROJECT_NAME.search(name):
            violations.append(f"{name} (path)")
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if OLD_PROJECT_NAME.search(line):
                violations.append(f"{name}:{lineno}")

    assert not violations, (
        "The project was renamed to Questly (src/questly, `questly.*` imports). "
        "Your branch predates the rename: `git pull origin dev`, move your code "
        "under src/questly/, fix the imports, and delete src/<old name>/. "
        "Found in:\n  " + "\n  ".join(violations)
    )

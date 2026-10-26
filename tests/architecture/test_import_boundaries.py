"""Architecture guards for module ownership."""

import ast
import importlib
from pathlib import Path

CORE_ROOT = Path("src/questly/core")

# Every slice now has a service layer: generation lost its exemption once its
# route started persisting a request/artifact through GenerationService instead
# of calling GenerationClient directly (OPS-12). Keep this set empty rather than
# deleting it, so the next bare-proxy slice has an obvious place to register.
ROUTE_SERVICE_EXCEPTIONS: set[str] = set()


def test_core_never_imports_ai() -> None:
    for source_file in CORE_ROOT.rglob("*.py"):
        assert "questly.ai" not in source_file.read_text(encoding="utf-8"), source_file


def test_core_never_imports_integrations() -> None:
    """Judge0 and email adapters are picked by main.py only (ADR-0007)."""
    violations = [
        str(source_file)
        for source_file in sorted(CORE_ROOT.rglob("*.py"))
        if "questly.integrations" in source_file.read_text(encoding="utf-8")
    ]

    assert not violations, (
        "core/ must reach Judge0/email through its own Protocol, never "
        "questly.integrations (AGENTS.md 'Ports and adapters'). Violations:\n"
        + "\n".join(violations)
    )


def test_core_never_imports_an_orm_or_storage_client() -> None:
    """`core/` sees a Protocol and a dataclass, never SQLModel or boto3."""
    forbidden = ("sqlmodel", "sqlalchemy", "boto3", "botocore", "questly.database")
    violations = []
    for source_file in sorted(CORE_ROOT.rglob("*.py")):
        text = source_file.read_text(encoding="utf-8")
        violations.extend(
            f"{source_file}: {name}" for name in forbidden if name in text
        )

    assert not violations, (
        "core/ must not import an ORM or a storage client (AGENTS.md "
        "'Layering'). Violations:\n" + "\n".join(violations)
    )


def _imported_modules(tree: ast.Module) -> list[str]:
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.append(node.module)
    return modules


def test_core_domain_layers_never_import_http_frameworks() -> None:
    """A service raises a domain error; only routes/pages turn it into HTTP."""
    forbidden = ("fastapi", "starlette")
    violations = []
    for layer in ("service.py", "models.py", "ports.py"):
        for source_file in sorted(CORE_ROOT.glob(f"*/{layer}")):
            tree = ast.parse(source_file.read_text(encoding="utf-8"))
            violations.extend(
                f"{source_file}: {module}"
                for module in _imported_modules(tree)
                if module.split(".")[0] in forbidden
            )

    assert not violations, (
        "core/<slice>/service.py, models.py and ports.py must not import "
        "FastAPI or Starlette (AGENTS.md 'Layering'). Raise a domain error and "
        "map it to HTTP in routes.py. Violations:\n" + "\n".join(violations)
    )


def test_routes_do_not_call_repository_ports_directly() -> None:
    for source_file in CORE_ROOT.glob("*/routes.py"):
        if source_file.parent.name in ROUTE_SERVICE_EXCEPTIONS:
            continue

        tree = ast.parse(source_file.read_text(encoding="utf-8"))
        port_imports = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module is not None
            and node.module.endswith(".ports")
        ]
        assert not port_imports, source_file


def test_every_slice_module_is_importable() -> None:
    slices = {
        "topics": ("models", "schemas", "ports", "service", "routes"),
        "assignments": (
            "models",
            "schemas",
            "ports",
            "service",
            "routes",
            "testcase_routes",
        ),
        "documents": ("models", "schemas", "ports", "service", "routes"),
        "generation": ("models", "schemas", "ports", "service", "routes", "pages"),
        "auth": (
            "models",
            "schemas",
            "ports",
            "service",
            "routes",
            "pages",
            "current",
        ),
        "classrooms": ("models", "schemas", "ports", "service", "routes", "pages"),
        "admin": ("models", "schemas", "ports", "service", "routes"),
    }
    for slice_name, modules in slices.items():
        for module in modules:
            importlib.import_module(f"questly.core.{slice_name}.{module}")

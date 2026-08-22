"""Dump the OpenAPI schema to a file.

Runs the FastAPI app's schema generation WITHOUT starting a server or touching
a database, so it works in CI and in a pre-commit hook.

This is the first half of the contract pipeline recorded in build-log Epic 0.3:
FastAPI is the source of truth, it emits OpenAPI, and TypeScript types are
generated from that. Hand-writing parallel type definitions in two languages is
a correctness hazard — they drift silently and the drift surfaces as a runtime
bug in the browser.

    uv run python scripts/export_openapi.py [output_path]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT = REPO_ROOT / "packages" / "shared-types" / "openapi.json"


def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

    from avp_api.config import Settings
    from avp_api.main import create_app

    # Explicit settings so schema generation never depends on a local .env.
    app = create_app(Settings(environment="local"))
    schema = app.openapi()

    output = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT
    output.parent.mkdir(parents=True, exist_ok=True)
    # sort_keys so the file is diff-stable: regenerating without an API change
    # must produce zero diff, or the generated file becomes noise in review.
    output.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    ops = sum(len(o) for o in schema["paths"].values())
    where = output.relative_to(REPO_ROOT)
    print(f"wrote {where}: {len(schema['paths'])} paths, {ops} operations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

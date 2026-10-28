#!/usr/bin/env python
"""Dump the DataLoom OpenAPI document to JSON.

DOCUMENTED ONLY - this script is never executed in the current project session.
It exists so that the frontend team has ONE documented way to regenerate the
OpenAPI JSON that TypeScript types are generated from.

Prerequisite: the FastAPI app object must be importable, i.e. the API package
plus ``dataloom-contracts`` must already be installed in a virtualenv.

Usage (run from the repository root, with the api venv active)::

    python packages/contracts/openapi_export.py \
        --out apps/web/openapi.json

Optional flags:
    --app-module   default ``app.main``  (module exposing ``app``)
    --app-attr     default ``app``
    --indent       default 2; use ``--indent 0`` for a compact file

The output is then turned into TypeScript types (NOT executed here either)::

    npx openapi-typescript apps/web/openapi.json -o apps/web/lib/api-types.ts

See packages/contracts/README.md for the full pipeline and its caveats.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path


def _load_app(module_name: str, attr: str):
    """Import ``module_name`` and return ``getattr(module, attr)``."""
    module = importlib.import_module(module_name)
    try:
        return getattr(module, attr)
    except AttributeError as exc:  # pragma: no cover - defensive
        raise SystemExit(
            f"Module '{module_name}' has no attribute '{attr}'. "
            "Pass --app-attr with the correct name."
        ) from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="Output path for openapi.json")
    parser.add_argument("--app-module", default="app.main")
    parser.add_argument("--app-attr", default="app")
    parser.add_argument("--indent", type=int, default=2)
    args = parser.parse_args(argv)

    application = _load_app(args.app_module, args.app_attr)

    # openapi() returns a plain dict; orjson cannot serialize it without
    # OPT_NON_STR_KEYS, so the stdlib json module is the simplest correct path.
    document = application.openapi()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if args.indent <= 0:
        payload = json.dumps(document, separators=(",", ":"), sort_keys=False)
    else:
        payload = json.dumps(document, indent=args.indent, sort_keys=False)
    out_path.write_text(payload + "\n", encoding="utf-8")

    # Print a short summary so CI logs show what was produced.
    paths = document.get("paths", {})
    schemas = document.get("components", {}).get("schemas", {})
    print(
        f"Wrote {out_path} ({len(paths)} paths, {len(schemas)} schemas)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Export the OpenAPI contract to web/openapi.json for frontend codegen.

The frontend types are generated from this snapshot (see web/orval.config.ts),
so the file is committed and CI verifies it still matches the code. Regenerate
after changing any endpoint's request/response shape:

    python scripts/export_openapi.py
    cd web && npm run gen:api
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def render() -> str:
    from spark.web.api_spec import OpenApiUnavailable, openapi_schema

    try:
        schema = openapi_schema()
    except OpenApiUnavailable as exc:
        sys.exit(
            f"{exc}\nInstall the optional extra first: pip install 'spark-agent[openapi]'"
        )
    return json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 when the committed snapshot differs from the code",
    )
    args = parser.parse_args()

    target = ROOT / "web" / "openapi.json"
    current = render()
    if args.check:
        existing = target.read_text(encoding="utf-8") if target.is_file() else ""
        if existing != current:
            print(
                "web/openapi.json is stale: run `python scripts/export_openapi.py` "
                "and `cd web && npm run gen:api`",
                file=sys.stderr,
            )
            return 1
        print("OpenAPI snapshot is up to date")
        return 0

    target.write_text(current, encoding="utf-8")
    print(f"wrote {target.relative_to(ROOT)} ({len(current)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

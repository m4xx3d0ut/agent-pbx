from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_pbx.operator_kb_sync import OperatorKbBundleV2Service


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate one deterministic Agent PBX Operator KB bundle v2.",
    )
    parser.add_argument("bundle", type=Path)
    args = parser.parse_args()
    with args.bundle.open("rb") as handle:
        raw = handle.read(16 * 1024 * 1024 + 1)
    if len(raw) > 16 * 1024 * 1024:
        raise SystemExit("KB bundle exceeds the 16 MiB validation limit")
    try:
        bundle = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"KB bundle is malformed: {exc}") from exc
    validated = OperatorKbBundleV2Service.validate_bundle(bundle)
    print(
        json.dumps(
            {
                "valid": True,
                "schema": validated["schema"],
                "bundle_sha256": validated["bundle_sha256"],
                "logical_project_id": validated["logical_project"][
                    "logical_project_id"
                ],
                "entry_count": len(validated["entries"]),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Local prompt validation and explicit bilingual-review bookkeeping."""

import argparse
import json
from pathlib import Path

from .maintenance import reviewed_manifest
from .registry import validate_registry


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "review"))
    parser.add_argument("--policy-version")
    parser.add_argument("--en-version")
    parser.add_argument("--zh-version")
    parser.add_argument("--reviewed-zh-policy")
    args = parser.parse_args()
    if args.command == "review":
        directory = Path(__file__).resolve().parent
        try:
            manifest = reviewed_manifest(
                directory, policy_version=args.policy_version, en_version=args.en_version,
                zh_version=args.zh_version, reviewed_zh_policy=args.reviewed_zh_policy,
            )
        except (ValueError, KeyError) as exc:
            parser.error(str(exc))
        (directory / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
    print(json.dumps(validate_registry(), ensure_ascii=False))


if __name__ == "__main__":
    main()

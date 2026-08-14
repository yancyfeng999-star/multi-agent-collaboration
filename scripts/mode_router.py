#!/usr/bin/env python3
"""Select the smallest collaboration mode from explicit task facts."""

from __future__ import annotations

import argparse
import json
from typing import Any


def select_mode(facts: dict[str, Any]) -> dict[str, Any]:
    """Return a deterministic, read-only routing decision.

    The router classifies work; it never grants release permission or creates
    governance records. Missing facts keep the decision on the Direct path.
    """

    writers = facts.get("independent_writers", 1)
    if isinstance(writers, bool) or not isinstance(writers, int) or writers < 1:
        raise ValueError("independent_writers must be an integer >= 1")

    emergency = bool(facts.get("emergency", False))
    quality = bool(facts.get("requires_independent_quality", False))
    cross_session = bool(facts.get("requires_cross_session_handoff", False))
    release_requested = bool(facts.get("requests_real_release", False))
    production = bool(facts.get("touches_production", False))

    reasons: list[str] = []
    if release_requested:
        reasons.append("real_release_requested")
    if production:
        reasons.append("production_action_requested")

    if release_requested or production:
        mode = "release"
        roles = ["integration_owner", "release"]
        persistence = "release_record"
    elif writers >= 2 or cross_session:
        if writers >= 2:
            reasons.append("multiple_independent_writers")
        if cross_session:
            reasons.append("cross_session_handoff")
        mode = "coordinated_emergency" if emergency else "coordinated"
        roles = ["coordinator", "owner"]
        if quality:
            reasons.append("independent_quality_required")
            roles.append("quality")
        persistence = "run"
    elif quality:
        mode = "reviewed"
        reasons.append("independent_quality_required")
        roles = ["owner", "quality"]
        persistence = "candidate"
    else:
        mode = "direct_hotfix" if emergency else "direct"
        roles = ["owner"]
        persistence = "none"

    return {
        "mode": mode,
        "upgrade_reasons": reasons,
        "required_roles": roles,
        "persistence_level": persistence,
        "execution_profile": "emergency" if emergency else "normal",
        "release_authorized": False,
        "read_only_decision": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--independent-writers", type=int, default=1)
    parser.add_argument("--emergency", action="store_true")
    parser.add_argument("--requires-independent-quality", action="store_true")
    parser.add_argument("--requires-cross-session-handoff", action="store_true")
    parser.add_argument("--requests-real-release", action="store_true")
    parser.add_argument("--touches-production", action="store_true")
    args = parser.parse_args()
    try:
        decision = select_mode(vars(args))
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(decision, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

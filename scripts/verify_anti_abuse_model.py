"""Offline specification model. No network, real authentication, concurrency or server enforcement."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict, deque
import json
from pathlib import Path


class ReferenceGate:
    """Models only the ordering/window semantics in document 21, with fabricated principals."""

    def __init__(self, raw_limit=20, coalesce_seconds=3, unique_limit=8, new_limit=10, source_limit=30):
        self.raw_limit = raw_limit
        self.coalesce_seconds = coalesce_seconds
        self.unique_limit = unique_limit
        self.new_limit = new_limit
        self.source_limit = source_limit
        self.principals = {}
        self.raw = defaultdict(deque)
        self.starts = defaultdict(deque)
        self.signs = defaultdict(deque)
        self.unique = defaultdict(dict)
        self.cache = {}
        self.sign_calls = 0

    def register(self, uid, items, epoch=1):
        self.principals[uid] = {"items": set(items), "epoch": epoch, "disabled": False}

    @staticmethod
    def trim(events, now, seconds):
        while events and events[0] <= now - seconds:
            events.popleft()

    def start(self, uid, item, now, *, epoch=1, context="verified-session", source="source-a",
              source_version="v1", profile="direct", signing_context="default"):
        principal = self.principals.get(uid)
        if not principal or principal["disabled"] or principal["epoch"] != epoch:
            return "DENY_AUTH"
        if item not in principal["items"]:
            return "DENY_ACL"
        attempts = self.raw[uid, item]
        self.trim(attempts, now, 10)
        if len(attempts) >= self.raw_limit:
            return "RATE_RAW"
        attempts.append(now)
        key = (uid, context, item, source, source_version, profile, epoch, signing_context)
        if self.cache.get(key, float("-inf")) > now:
            return "REUSE_AUTHORIZED"
        starts = self.starts[uid]
        self.trim(starts, now, 60)
        if len(starts) >= self.new_limit:
            return "RATE_NEW_START"
        unique = self.unique[uid]
        for old in [i for i, timestamp in unique.items() if timestamp <= now - 600]:
            del unique[old]
        if item not in unique and len(unique) >= self.unique_limit:
            return "RATE_UNIQUE"
        signs = self.signs[source]
        self.trim(signs, now, 60)
        if len(signs) >= self.source_limit:
            return "RATE_SOURCE"
        starts.append(now)
        signs.append(now)
        unique[item] = now
        self.cache[key] = now + self.coalesce_seconds
        self.sign_calls += 1
        return "SIGN_NEW"

    def segment(self, uid, item, epoch=1):
        """Only verifies no new-sign accounting; real segment budgets/leases are outside this model."""
        principal = self.principals.get(uid)
        if not principal or principal["disabled"] or principal["epoch"] != epoch:
            return "DENY_AUTH"
        return "SEGMENT_EXISTING" if item in principal["items"] else "DENY_ACL"


def verify():
    checks = []

    def record(name, actual, expected):
        if actual != expected:
            raise AssertionError(f"{name}: actual={actual!r}, expected={expected!r}")
        checks.append({"scenario": name, "actual": actual, "expected": expected, "passed": True})

    gate = ReferenceGate()
    gate.register("user-a", ["episode-1"])
    burst = dict(Counter(gate.start("user-a", "episode-1", 0) for _ in range(100)))
    record("100 simultaneous repeated controls", burst,
           {"SIGN_NEW": 1, "REUSE_AUTHORIZED": 19, "RATE_RAW": 80})
    record("only one upstream sign for burst", gate.sign_calls, 1)
    gate.principals["user-a"]["disabled"] = True
    record("revoked principal cannot use cached redirect", gate.start("user-a", "episode-1", 1), "DENY_AUTH")
    gate.principals["user-a"]["disabled"] = False
    gate.principals["user-a"]["epoch"] = 2
    record("old epoch denied despite cache", gate.start("user-a", "episode-1", 1), "DENY_AUTH")

    gate = ReferenceGate()
    gate.register("user-a", ["episode-1"])
    record("ten normal same-context retries", dict(Counter(gate.start("user-a", "episode-1", 0) for _ in range(10))),
           {"SIGN_NEW": 1, "REUSE_AUTHORIZED": 9})
    before = gate.sign_calls
    record("120 authorized segments are not new starts", dict(Counter(gate.segment("user-a", "episode-1") for _ in range(120))),
           {"SEGMENT_EXISTING": 120})
    record("segments did not increase signing", gate.sign_calls, before)
    record("foreign item denied even while other item cached", gate.start("user-a", "foreign", 1), "DENY_ACL")
    gate.register("user-b", ["episode-1"])
    record("second user gets separate authorized result", gate.start("user-b", "episode-1", 1), "SIGN_NEW")
    record("source version change is not reused", gate.start("user-a", "episode-1", 1, source_version="v2"), "SIGN_NEW")
    record("profile change is not reused", gate.start("user-a", "episode-1", 1, profile="other"), "SIGN_NEW")
    record("signing context change is not reused", gate.start("user-a", "episode-1", 1, signing_context="bound-context"), "SIGN_NEW")

    gate = ReferenceGate()
    gate.register("user-a", [f"item-{n}" for n in range(12)])
    record("twelve distinct items within ten minutes", dict(Counter(gate.start("user-a", f"item-{n}", n) for n in range(12))),
           {"SIGN_NEW": 8, "RATE_UNIQUE": 4})
    record("ten-minute left boundary releases distinct item", gate.start("user-a", "item-8", 600), "SIGN_NEW")
    gate = ReferenceGate(new_limit=1)
    gate.register("user-a", ["a", "b"])
    record("first limited start", gate.start("user-a", "a", 0), "SIGN_NEW")
    record("new-start budget prevents next item", gate.start("user-a", "b", 1), "RATE_NEW_START")
    record("sixty-second left boundary releases start budget", gate.start("user-a", "b", 60), "SIGN_NEW")
    gate = ReferenceGate(source_limit=1)
    gate.register("user-a", ["a"])
    gate.register("user-b", ["b"])
    gate.start("user-a", "a", 0)
    record("source budget shared across users", gate.start("user-b", "b", 1), "RATE_SOURCE")
    gate = ReferenceGate()
    gate.register("user-a", ["a"])
    gate.start("user-a", "a", 0)
    record("coalescing interval is end-exclusive", gate.start("user-a", "a", 3), "SIGN_NEW")
    record("anonymous access denied", gate.start("unknown", "a", 4), "DENY_AUTH")
    return {
        "model_version": "v6-2026-11-18",
        "scope": "serial offline reference, enforcing candidate limits only; production behavior initially observe",
        "network_requests": 0,
        "limitations": ["no real authentication or server", "no thread/process atomicity test", "no range protocol or segment rate test",
                        "no cache eviction, in-flight waiting, DDoS or end-to-end client test"],
        "burst_summary": burst,
        "checks": checks,
        "passed": all(c["passed"] for c in checks),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = verify()
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8", newline="\n")
        print(json.dumps({"passed": result["passed"], "checks": len(result["checks"]), "burst": result["burst_summary"]}))
    else:
        print(encoded, end="")


if __name__ == "__main__":
    main()

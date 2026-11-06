#!/usr/bin/env python3
"""CI gate: published versions are immutable, and trust documents never roll back.

Run from the vcp-hub repository root after a full-history checkout.

Published-version immutability (pull requests, ``--base`` = PR base SHA):
every path under a ``namespaces/<ns>/<id>/<version>/`` directory that already
exists on the base branch is frozen. Additions, modifications, deletions and
renames there all fail, with exactly three exceptions:

- ``entry.json`` may change ONLY in ``trust_tier`` and
  ``distribution.counter_signatures`` (verified-tier counter-signing ceremony,
  GOVERNANCE.md); every other field (including ``distribution.artifact``,
  ``content_sha256``, ``distribution.signatures``, ``name`` and ``description``)
  must equal the base revision, and ``trust_tier`` may only go UP;
- ``<artifact>.ed25519.countersig`` for the artifact the base entry names may be
  added or replaced, never deleted (``vcp lint`` verifies it against the pinned
  root and checks it agrees with ``trust_tier``);
- ``<artifact>.ed25519.sig`` may be replaced ONLY by the ref-binding migration:
  a legacy v1 sidecar re-signed as v2 with the same algorithm, key_id and
  content_hash and a ``ref`` naming exactly this directory (``vcp lint``
  verifies the signature itself).

Anti-rollback (pull requests and pushes): ``namespace_registry.json`` and
``index.json`` may not carry a lower ``sequence`` than the base revision, and a
changed ``namespace_registry.json`` must carry a strictly higher one, so an
older root-signed copy from git history cannot be swapped back in.

Standard library only, so the gate does not depend on the client release.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from typing import Any

REGISTRY_FILE = "namespace_registry.json"
INDEX_FILE = "index.json"
CEREMONY_ENTRY_FIELDS = ("trust_tier",)
CEREMONY_DISTRIBUTION_FIELDS = ("counter_signatures",)
# A published version's tier may be RAISED by the counter-signing ceremony,
# never lowered.
TIER_RANK = {"signed": 1, "verified": 2}
LEGACY_SIG_VERSION = 1  # content-only publisher sidecar (pre ref binding)
BOUND_SIG_VERSION = 2  # ref-bound publisher sidecar


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def _blob(rev: str, path: str) -> bytes | None:
    """Bytes of ``path`` at ``rev``, or None when it does not exist there."""
    proc = subprocess.run(["git", "cat-file", "-e", f"{rev}:{path}"], capture_output=True)
    if proc.returncode != 0:
        return None
    return subprocess.run(["git", "show", f"{rev}:{path}"], check=True, capture_output=True).stdout


def _head_blob(path: str) -> bytes | None:
    return _blob("HEAD", path)


def _version_dirs(rev: str) -> set[str]:
    dirs = set()
    # -z: raw, unquoted paths (quoted paths would otherwise dodge the prefix match).
    for path in _git("ls-tree", "-r", "-z", "--name-only", rev, "--", "namespaces/").split("\0"):
        parts = path.split("/")
        if len(parts) >= 5:
            dirs.add("/".join(parts[:4]))
    return dirs


def _load_json(raw: bytes | None, label: str, problems: list[str]) -> dict[str, Any] | None:
    if raw is None:
        return None
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        problems.append(f"{label}: not valid JSON ({exc})")
        return None
    if not isinstance(doc, dict):
        problems.append(f"{label}: not a JSON object")
        return None
    return doc


def _frozen_entry_view(entry: dict[str, Any]) -> dict[str, Any]:
    """The entry minus the fields the counter-signing ceremony may change."""
    view = {k: v for k, v in entry.items() if k not in CEREMONY_ENTRY_FIELDS}
    dist = view.get("distribution")
    if isinstance(dist, dict):
        view["distribution"] = {k: v for k, v in dist.items() if k not in CEREMONY_DISTRIBUTION_FIELDS}
    return view


def check_immutability(base: str) -> list[str]:
    problems: list[str] = []
    merge_base = _git("merge-base", base, "HEAD").strip()
    existing = _version_dirs(base) | _version_dirs(merge_base)
    fields = _git("diff", "-z", "--no-renames", "--name-status", merge_base, "HEAD", "--", "namespaces/").split("\0")
    for status, path in zip(fields[0::2], fields[1::2]):
        version_dir = "/".join(path.split("/")[:4])
        if version_dir not in existing:
            continue  # a brand-new version directory: vcp lint checks it in full
        old_rev = merge_base if _blob(merge_base, f"{version_dir}/entry.json") is not None else base
        base_entry = _load_json(_blob(old_rev, f"{version_dir}/entry.json"), f"{version_dir}/entry.json@base", problems)
        artifact = (base_entry or {}).get("distribution", {}).get("artifact")
        filename = path[len(version_dir) + 1 :]

        if filename == "entry.json" and status == "M":
            head_entry = _load_json(_head_blob(path), path, problems)
            if base_entry is None or head_entry is None:
                problems.append(f"{path}: cannot compare against the published entry")
                continue
            if _frozen_entry_view(base_entry) != _frozen_entry_view(head_entry):
                changed = sorted(
                    k
                    for k in set(base_entry) | set(head_entry)
                    if _frozen_entry_view(base_entry).get(k) != _frozen_entry_view(head_entry).get(k)
                )
                problems.append(
                    f"{path}: published entry fields changed {changed}; only trust_tier and "
                    "distribution.counter_signatures may change (counter-signing ceremony)"
                )
            base_tier, head_tier = base_entry.get("trust_tier"), head_entry.get("trust_tier")
            if _tier_rank(head_tier) < _tier_rank(base_tier):
                problems.append(
                    f"{path}: trust_tier lowered ({base_tier!r} -> {head_tier!r}) on a published "
                    "version; a tier is never downgraded in place, publish a new version"
                )
            continue
        if isinstance(artifact, str) and filename == f"{artifact}.ed25519.countersig":
            if status.startswith("D"):
                problems.append(
                    f"{path!r}: counter-signature deleted under a published version; the "
                    "verified tier is never withdrawn by a change"
                )
            continue  # added/replaced: counter-signing ceremony; vcp lint verifies it against the root
        if isinstance(artifact, str) and filename == f"{artifact}.ed25519.sig" and status == "M":
            problem = _sidecar_upgrade_problem(old_rev, version_dir, path)
            if problem is None:
                continue  # v1 -> v2 re-sign of the same bytes; vcp lint verifies the signature
            problems.append(f"{path!r}: file modified under a published version ({problem})")
            continue
        problems.append(f"{path!r}: {_describe(status)} under a published version")
    return problems


def _tier_rank(tier: Any) -> int:
    """Higher is stronger; anything unrecognised ranks below every known tier."""
    return TIER_RANK.get(tier, -1) if isinstance(tier, str) else -1


def _version_dir_ref(version_dir: str) -> str | None:
    parts = version_dir.split("/")
    if len(parts) != 4 or parts[0] != "namespaces":
        return None
    _, namespace, artifact_id, version = parts
    return f"{namespace}/{artifact_id}@{version}"


def _sidecar_upgrade_problem(old_rev: str, version_dir: str, path: str) -> str | None:
    """None iff ``path`` is exactly a legacy v1 -> ref-bound v2 re-sign of the SAME bytes.

    The ref-binding migration (verify.LEGACY_UNBOUND_ARTIFACT_PINS) needs every
    shipped v1 publisher sidecar re-signed as v2 in place. This gate cannot check
    Ed25519 (stdlib only); it pins everything else: base is v1, head is v2, same
    algorithm, same key_id, same content_hash, and head ``ref`` names exactly this
    directory. ``vcp lint`` in the same CI run verifies the new signature against
    the namespace's registered keys and its ref binding.
    """
    scratch: list[str] = []
    base_sig = _load_json(_blob(old_rev, path), f"{path}@base", scratch)
    head_sig = _load_json(_head_blob(path), path, scratch)
    if base_sig is None or head_sig is None:
        return "; ".join(scratch) or "sidecar unreadable"
    if base_sig.get("version") != LEGACY_SIG_VERSION or head_sig.get("version") != BOUND_SIG_VERSION:
        return (
            f"only a v{LEGACY_SIG_VERSION} -> v{BOUND_SIG_VERSION} re-sign may replace a published "
            f"sidecar (base v{base_sig.get('version')!r}, head v{head_sig.get('version')!r})"
        )
    for field in ("algorithm", "key_id", "content_hash"):
        if not isinstance(head_sig.get(field), str) or head_sig.get(field) != base_sig.get(field):
            return f"re-signed sidecar changed {field!r}"
    expected_ref = _version_dir_ref(version_dir)
    if expected_ref is None or head_sig.get("ref") != expected_ref:
        return f"re-signed sidecar ref {head_sig.get('ref')!r} is not {expected_ref!r}"
    return None


def _describe(status: str) -> str:
    return {"A": "file added", "M": "file modified", "D": "file deleted", "T": "file type changed"}.get(
        status[:1], f"change {status!r}"
    )


def _sequence(doc: dict[str, Any] | None) -> int | None:
    if doc is None:
        return None
    seq = doc.get("sequence")
    return seq if isinstance(seq, int) and not isinstance(seq, bool) else None


def check_sequences(base: str) -> list[str]:
    problems: list[str] = []
    for path, must_bump_on_change in ((REGISTRY_FILE, True), (INDEX_FILE, False)):
        base_raw = _blob(base, path)
        if base_raw is None:
            continue
        head_raw = _head_blob(path)
        if head_raw is None:
            problems.append(f"{path}: removed; the root-signed trust document cannot be deleted by a change")
            continue
        base_seq = _sequence(_load_json(base_raw, f"{path}@base", problems))
        head_seq = _sequence(_load_json(head_raw, path, problems))
        if base_seq is None or head_seq is None:
            problems.append(f"{path}: missing or invalid integer sequence (base {base_seq}, head {head_seq})")
            continue
        if head_seq < base_seq:
            problems.append(f"{path}: sequence went BACKWARDS ({base_seq} -> {head_seq}); rollback refused")
        elif must_bump_on_change and head_raw != base_raw and head_seq == base_seq:
            problems.append(
                f"{path}: content changed without a sequence bump ({base_seq}); only the root "
                "signing ceremony (scripts/build_namespace_registry.py) may change it"
            )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="base revision (PR base SHA, or the pre-push SHA)")
    parser.add_argument(
        "--sequences-only",
        action="store_true",
        help="check only trust-document sequences (push events; immutability is a PR gate)",
    )
    args = parser.parse_args(argv)

    # Fail closed: an unknown base (shallow clone, force-pushed-away SHA) would
    # otherwise make every base-side lookup look like "file absent" and pass.
    verify_base = ["git", "rev-parse", "--verify", "--quiet", f"{args.base}^{{commit}}"]
    if subprocess.run(verify_base, capture_output=True).returncode != 0:
        print(f"::error::base revision {args.base!r} is not available; refusing to skip the check")
        return 1

    problems = check_sequences(args.base)
    if not args.sequences_only:
        problems += check_immutability(args.base)
    for problem in problems:
        print(f"::error::{problem}")
    if problems:
        print("published versions are immutable and trust documents never roll back; bump the version instead")
        return 1
    print("immutability + anti-rollback: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

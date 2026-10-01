# Creed Commons — vcp-hub

An open registry of **signed value artifacts** for AI systems: constitutions,
creeds, and detector configurations, distributed the way package registries
distribute packages — with one structural difference that is the whole point:

> **Every artifact here is data that a values engine interprets. Nothing here is
> executable code.** Installing an artifact is a signature-verification decision,
> not a code-execution one. `vcp install` verifies an Ed25519 signature against a
> key allowlist pinned inside the client, a content sha256, and the registry-entry
> schema — and never imports, evals, or executes what it fetched. There are no
> post-install hooks.

## Installing

```bash
pip install "vcp-sdk[hub]"
vcp search gaslighting
vcp install creed-space/anti_gaslighting        # latest
vcp install creed-space/uef@1.0.0               # pinned
vcp verify                                       # re-check installed tree against vcp.lock
```

`vcp install` writes the artifact, its signature, and its registry entry under the
target directory and pins `name@version` + content sha256 in `vcp.lock`.
`vcp verify` fails on any drift between the lockfile and what is on disk.

## Trust tiers — read this before trusting an artifact

| Tier | Meaning |
|------|---------|
| `signed` | The artifact's Ed25519 signature verifies against a pinned Creed Space publisher key, its content hash matches its registry entry, and the entry is schema-valid. **This does NOT vouch for the artifact's semantics** — only that it is exactly what its publisher signed. |
| `verified` | Everything `signed` guarantees, **plus** constitution lint, red-team evaluation, and a domain-separated Creed Space **counter-signature** that the client checks against the pinned root. This is the semantic gate — issuance criteria are in [GOVERNANCE.md](GOVERNANCE.md). |

The client displays the tier at install time. A `signed` artifact is never
presented as `verified`.

## Namespace policy — community publishing is OPEN

Namespaces are governed by the root-signed **`namespace_registry.json`**: each
namespace is bound to its publisher's Ed25519 keys, the binding is signed by
the pinned Creed Space root, and clients refuse artifacts not signed by a key
registered to their namespace. To register yours, open a PR using the
namespace-registration template — name rules, review, the signing ceremony,
moderation, and revocation are all specified in [GOVERNANCE.md](GOVERNANCE.md).
Confusable variants of existing names (`creed_space`, `creedspace`, homoglyph
squats) are rejected by the client and CI, not just by policy.

## Publishing rules

1. **Signed only.** Every artifact ships with a `.ed25519.sig` sidecar that
   verifies against a pinned publisher key. CI rejects unsigned artifacts —
   there is no exception path.
2. **Versions are immutable.** Once `name@version` is published, its bytes never
   change. Fixes bump the version. CI rejects any PR that modifies files under
   an existing version directory.
3. **Schema-valid entries.** Every `entry.json` must validate against the
   registry-entry schema (bundled with the vcp-sdk client) including the
   distribution block (`namespace`, `content_sha256`, `distribution`,
   `trust_tier`).
4. **Data, never code.** Artifacts are Markdown/JSON interpreted by the Creed
   Space engine. Anything resembling executable content is rejected in review.
5. **PR flow.** `vcp publish <artifact.md> --hub .` generates the version
   directory and regenerates `index.json`; open a PR and CI runs the same lint
   locally reproducible with `vcp lint .`.

## Layout

```
index.json                       generated — never edit by hand (vcp build-index .)
namespace_registry.json          namespace -> publisher keys, ROOT-SIGNED
namespace_registry.json.ed25519.sig
namespaces/<namespace>/<id>/<version>/
    <artifact>                   the signed artifact (data)
    <artifact>.ed25519.sig       publisher signature sidecar
    <artifact>.ed25519.countersig  root counter-signature (verified tier only)
    entry.json                   schema-valid registry entry
keys/                            publisher public keys FOR DISPLAY ONLY
GOVERNANCE.md                    registration, moderation, tiers, revocation
```

**Trust-root note:** the keys in `keys/` are published for human inspection.
They are **not** the trust root — the `vcp` client ships its own pinned
allowlist precisely so this registry cannot vouch for itself.

## Provenance

Artifacts are authored and signed in Creed Space's source repository
(Creed-Space/Rewind, which is private) and seeded here via
`scripts/seed_vcp_hub.py` in that repository. The signing keys are Creed
Space's `constitution-signer` (+ backup) Ed25519 keys. Clients verify
signatures against the pinned keys and do not need access to the source
repository.

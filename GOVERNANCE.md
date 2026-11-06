# Creed Commons Governance

How namespaces are registered, how artifacts are moderated, how the `verified`
tier is issued, and how anything is revoked. The mechanisms below are enforced
by the `vcp` client and this repo's CI; the human judgments are made in the PR
flow, in public.

## Trust model (what enforces what)

- **Root of trust**: the Creed Space publisher keys pinned inside the `vcp`
  client. Nothing served by this registry can extend that root.
- **Delegation**: `namespace_registry.json` binds each namespace to its
  publisher's Ed25519 public keys, and is itself root-signed with its own
  signature domain. Clients refuse a registry that does not verify. An
  artifact in namespace X must verify against a key registered to X — keys
  never work across namespaces, and community registrations may not reuse a
  root key id or root key material (client-refused, not just policy).
- **Signed metadata**: `index.json` (version resolution, hashes, tiers) is
  root-signed in its own domain and carries a monotonic **sequence**; clients
  record the highest sequence seen in `vcp.lock` (both `vcp install` and
  `vcp verify --registry` raise it) and refuse anything lower, so a
  validly-signed but stale index or registry cannot be replayed to re-admit
  revoked keys or downgrade versions after first contact. The index also
  carries a root-signed `issued_at` (and optional `expires_at`); clients
  refuse an index older than 30 days (configurable via
  `RegistryClient(max_index_age=...)`), so **the release ceremony must
  re-sign index.json at least every 30 days**. The one pre-freshness index
  (sequence 2) is accepted by exact hash until it is re-signed. The unsigned
  `entry.json` is cross-checked against the signed index at install time.
- **Tier issuance**: `signed` is proven by the publisher's signature.
  `verified` is proven ONLY by a root counter-signature
  (`<artifact>.ed25519.countersig`) that is domain-separated AND bound to the
  exact `namespace/id@version`, so neither a publisher signature nor a
  counter-signature for another ref can be replayed. Editing `entry.json`
  cannot grant a tier: the tier must also match the signed index.
- **Lockfile boundary**: `vcp.lock` pins what was verified at install time and
  `vcp verify` detects any drift from it. Like every lockfile, it is local
  state: an attacker who can rewrite BOTH the lock and the installed tree is
  outside its threat model. `vcp verify --registry <url>` closes even that by
  re-checking every pin against the live root-signed index.
- **Residual risk, stated honestly**: a client's very FIRST contact with a
  registry has no sequence high-water mark, so a transport attacker could
  serve it a stale-but-signed snapshot once, and a client that never saw a
  revoking release can be shown the previous index until that index ages
  past the max age. Subsequent installs and verifies detect any rollback
  below what they have seen. (Narrowing the window further needs a TUF-style
  timestamp role with short expiry; adopt it if the ecosystem grows to
  warrant it.)

## Registering a community namespace

1. **Open a PR** using the namespace-registration template. It must contain:
   - the namespace name;
   - your Ed25519 **public** key(s) in PEM (SubjectPublicKeyInfo), with a key id;
   - a contact address;
   - a statement that you hold the private key and accept this policy.
2. **Name rules** (client- and CI-enforced): `[a-z][a-z0-9-]*`, 3-63 chars,
   not reserved (`creed*`, `vcp*`, `official`, `admin`, `verified`, `signed`,
   trust/infrastructure terms), and not confusable with any existing or
   reserved name after normalization (case, `-`/`_` stripping, homoglyph
   folding: `0→o 1→l 3→e 4→a 5→s 7→t 8→b 9→g`). First come, first served.
3. **Review** (a maintainer checks, typically within a week): name policy,
   plausible publisher identity, key well-formedness, no impersonation of an
   organization you do not represent.
4. **Signing ceremony**: a maintainer applies the entry with
   `scripts/build_namespace_registry.py --add …` in the source repo, which
   bumps the registry sequence, re-signs `namespace_registry.json` with the
   root key, and self-verifies as a client would; releases then run
   `scripts/hub_release.py` to bump and re-sign the index. Only maintainers
   can complete a registration — a merged PR without the re-signed registry
   has no effect, by construction.

## Publishing artifacts

Everything in the README's publishing rules, plus:

- Artifacts in your namespace must be signed by a key registered to your
  namespace. CI (`vcp lint .`) refuses anything else.
- The publisher signature is bound to the exact ref: it signs
  `"creed-commons-artifact-v2\0" + "<namespace>/<id>@<version>" + "\0" + <artifact bytes>`
  (`vcp.hub.artifact_message_context(ref)` builds the prefix), and the
  `.ed25519.sig` sidecar carries `"version": 2` and `"ref"`. Signed bytes
  therefore cannot be republished under another namespace, id, or version.
  Content-only (v1) signatures are honoured only for the artifacts shipped
  before ref binding, pinned in the client by exact ref and hash.
- Versions are immutable — in content AND provenance. Changing bytes or the
  recorded signer of a published version is refused; bump the version. CI
  freezes every file under an existing version directory (additions included);
  only the counter-signing ceremony may change `entry.json`'s `trust_tier` and
  `distribution.counter_signatures` and add the `.ed25519.countersig`.
- Only `trust_tier: signed` may be self-published. The `verified` tier is
  issued by Creed Space (below).

## Trust tiers

| Tier | Grants | Issued by |
|------|--------|-----------|
| `signed` | Integrity + origin: the bytes are exactly what the registered publisher signed. Says NOTHING about semantics. | The publisher's own signature. |
| `verified` | Everything above, plus: constitution lint clean; red-team evaluation against the Creed Space guardian benchmark; human review of scope and framing. | A domain-separated root counter-signature (`scripts/countersign_artifact.py` ceremony), on request, free, at Creed Space's discretion. |

The client displays the tier at install time and re-checks the
counter-signature on `vcp verify`. A `signed` artifact is never presented as
`verified`.

## Moderation

- **Scope**: this registry distributes constitutions, creeds, and detector
  configurations — value declarations interpreted by an engine. At the
  `signed` tier we do NOT semantically endorse content; moderation is
  reactive.
- **Report**: open an issue titled `[report] namespace/id@version` with the
  concern. Malicious content (artifacts crafted to steer systems toward harm,
  impersonation, deception about provenance) is removed; contested value
  positions that are honestly labeled generally are not — this registry hosts
  plural value systems by design.
- **Actions available** (proportionate, in order): maintainer contact with the
  publisher → version removal from tree + index → namespace key revocation
  (removal from the signed `namespace_registry.json`, which blocks all future
  installs from that namespace) → namespace removal.
- **Already-installed copies**: a lockfile pins what was verified at install
  time, so removal does not retroactively break installed trees (`vcp verify`
  keeps passing against the lockfile). Re-installs and new installs are
  blocked once the registry no longer admits the namespace.

## Key management

- **Publisher key loss/compromise**: report it immediately; the key is removed
  from the registry (blocking future installs signed by it) and a new key can
  be registered by the same process as registration.
- **Root key**: rotated by re-pinning in a new `vcp-sdk` release, never via
  this registry (the registry cannot update the root that verifies it —
  deliberately). Root rotation re-signs all first-party artifacts.
- **Incident note (2026-07-12)**: an earlier development configuration stored
  key material in an OS temp directory, which silently rotated derived keys
  when purged. Key derivation is now anchored to durable storage; the pinned
  root was recovered, not rotated. Recorded here because key-handling
  transparency is the product.

## Changing this document

By PR to this repo. Material changes to enforcement (name rules, tier
criteria, revocation semantics) also require a matching `vcp-sdk` release,
since the client enforces them.

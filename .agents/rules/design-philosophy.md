# Design Philosophy

How to choose work; repo-wide invariants remain in [`project.md`](project.md).

- **Require an observed need before adding a feature or safeguard.** Record the specific failure,
  the simplest adequate remedy, and which existing implementation already solves it. Possibility
  alone is not evidence. Keep reusable reasoning in references, not in binding rules.
  [Project decisions](../references/202610122120_dataset-plane-decisions.md).
- **Delete redundancy before adding another mechanism.** Two stores or definitions of the same fact
  can drift; prefer removing a path to maintaining both. Delay speculative work and name the trigger
  that would justify returning to it. [Deferred scope](project.md#scope-and-guidance).
- **Search the repository, dependencies and platform before implementing.** Audit an upstream script
  before replacing it, and reuse the existing mapping/decoder rather than forking one beside it.
  [Decoder reuse](../references/2026102115_documentation-and-implementation-alignment.md).
- **Prefer platform primitives and direct identities.** Public object URLs avoid a proxy; canonical
  request text avoids a cache-key digest; opaque platform ETags avoid local checksums. Size/header
  checks are not byte-equality or duplicate detection. Do not claim stronger guarantees than the
  implementation provides. [Publisher/cache decision](../references/2026102115_documentation-and-implementation-alignment.md).

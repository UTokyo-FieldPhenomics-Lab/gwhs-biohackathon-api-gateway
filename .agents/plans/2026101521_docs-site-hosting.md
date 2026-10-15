# Docs site: publish Zensical output on GitHub Pages at `docs.global-wheat.com`

Status: Phase 1 done and live on 2026-10-15 at <https://docs.global-wheat.com>; Phase 2 partly done.
Execution record: [`../references/2026101516_docs-site-hosting.md`](../references/2026101516_docs-site-hosting.md).
The pipeline proof took one real failure to get right — a build that emitted no `site/assets/` — and
the cause and the fix are recorded below and in that file. Remaining work is listed under
[Result](#result-2026-10-15).

## Goal and acceptance

Participants read the curated documentation at `https://docs.global-wheat.com/`, served by GitHub
Pages from an Actions artifact, reachable without the gateway host being up. `api.global-wheat.com/docs`
stays FastAPI's own Swagger UI: the site links to it, and (Phase 2) it links back.

Acceptance, all observable from a machine that is not the contributor's:

| # | Check | Expected |
|---|---|---|
| 1 | `curl -sI https://docs.global-wheat.com/` | `HTTP/2 200`, `server: GitHub.com` |
| 2 | `curl -sI http://docs.global-wheat.com/` | `301` to the `https` URL |
| 3 | `curl -sI https://utokyo-fieldphenomics-lab.github.io/gwhs-biohackathon-api-gateway/` | `301` to `https://docs.global-wheat.com/` — **re-run after the custom domain is saved**: until then the path 301s host-wide to `lab.fieldphenomics.com/<repo>/`, because the org user site has its own custom domain |
| 4 | push touching `docs/**` | workflow green, deploy job ran, change live at the custom domain |
| 5 | `uv run --project docs zensical build --strict` (repo root) | exit 0; `git status --porcelain` afterwards is empty |
| 6 | `openssl s_client -connect docs.global-wheat.com:443 -servername docs.global-wheat.com` | a certificate whose subject is `docs.global-wheat.com` — not GitHub's wildcard `CN=*.github.io` |

## Facts verified while planning

Measurements taken on 2026-10-15, so execution does not re-derive them:

| Fact | Evidence |
|---|---|
| The repository is public, default branch `main`, MIT | GitHub REST API returns `"private": false`; the Pages API for it is `404`, so Pages is not enabled yet |
| The zone is at お名前.com served by `dnsv.jp`, **not** Route 53 | `dns.google` NS query; `deploy/README.md` §2.2 records the same |
| `api.*/docs` is Swagger and an open path | `gateway/src/biohack_gateway/app.py:50` (`OPEN_PATHS`), `:200` (`FastAPI(...)` with defaults) |
| Zensical resolves `docs_dir` and `site_dir` relative to the config file | probe: config at a project root with `docs/` + `site/` builds; output landed in `site/` |
| A minimal config still renders admonitions, fenced code, and task lists | probe: `!!! note`, a `python title=` fence, and `- [x]` all rendered with only `[project]` + `[project.theme]` set |
| `uv run --project docs …` works from the repository root | `uv run --project docs zensical --version` → `0.0.69`; `uv sync --locked --project docs` → 13 packages, clean |
| `.gitignore` already ignores the build output | line 170, `/site`, under the heading "mkdocs/Zensical documentation" |
| Actions publishing needs no `CNAME` file in the repo | docs.github.com: with a custom Actions workflow no `CNAME` file is created and any existing one is ignored |
| The custom domain must be saved in Settings **before** the DNS record exists | docs.github.com warning: the reverse order lets someone else host a site on the subdomain |
| Verifying a domain protects its immediate subdomains | docs.github.com: verifying `github.com` protects `docs.github.com` and `support.github.com`, so the `TXT` step is redundant if the organization has already verified `global-wheat.com` |
| The organization already publishes a Pages site on this zone, the same way | live DNS on 2026-10-15: `global-rice.com` → the four Pages `A` addresses, `www.global-rice.com` → `CNAME utokyo-fieldphenomics-lab.github.io` — the exact value our `docs` record needs, proven against the same panel |
| `docs.global-wheat.com` does not exist yet | `dns.google` → NXDOMAIN, and no `_github-pages-challenge-utokyo-fieldphenomics-lab.global-wheat.com` TXT either (so the domain is not verified today, as far as public DNS shows) |
| The zone already carries a `www` record for a different repository's Pages site | live DNS: `www.global-wheat.com` → `CNAME utokyo-fieldphenomics-lab.github.io`; the org's API lists a repository named `www.global-wheat.com` with `has_pages: true` and that homepage. Unrelated to ours: Pages attaches a custom domain per repository |
| That neighbouring site's HTTPS is broken today | `https://www.global-wheat.com/` is answered with GitHub's wildcard `CN=*.github.io`, whose `subjectAltName` omits the name, so every HTTPS client fails, while `http://` returns `200` with content dated 2026-05-01. A resolving `CNAME` and a pending certificate look identical from DNS — this is the state §6.3 checks for |
| The organization's user site redirects its whole hostname | `https://utokyo-fieldphenomics-lab.github.io/<repo>/` → `301 https://lab.fieldphenomics.com/<repo>/`. It stops redirecting elsewhere once a repository has a custom domain of its own, as `www.global-wheat.com` shows |
| `Enforce HTTPS` is unavailable until the certificate is issued | docs.github.com: up to 24 hours |

## Decisions (locked)

| Decision | Reason |
|---|---|
| Host: GitHub Pages | Zero infrastructure, free on a public repo, no secret in CI, and independent of the on-demand EC2 host and of the EIP that one measured network already drops. `data.*` is write-once and must never need invalidation; the site is rewritten on every push, so it must not share that plane. |
| Domain: `docs.global-wheat.com`, `CNAME` → `utokyo-fieldphenomics-lab.github.io` | One hand record in the same panel that already holds `api` and `data`, in exchange for a printable, rename-proof URL. A subdomain takes `CNAME`; the value never includes the repository name. |
| Publishing source: an Actions workflow, artifact-based | Bypasses Jekyll, needs no `gh-pages` branch, and keeps the build reproducible from `docs/uv.lock`. |
| Config lives at the repository root as `zensical.toml`, `docs_dir = "docs/content"`, `site_dir = "site"` | Zensical resolves both relative to the config file, and `.gitignore` already ignores root `/site`. The content root is `docs/content/` and not `docs/` because a virtualenv under `docs_dir` makes the build emit no `site/assets/` at all — measured, and the reason Phase 1 initially shipped an unstyled site. |
| No `docs/README.md` | Anything under `docs_dir` is site material; the runbook belongs in `deploy/README.md` beside the bucket and CDN procedures. |
| The site is public, so nothing secret may ever enter `docs/` | The repository is public and the artifact is world-readable. |
| `api.*/docs` is untouched | It is the interactive reference and an already-published surface; the curated site is a separate artifact. |

## Out of scope

- `gateway/scripts/gen_api_docs.py`, and any generated API reference page on the site. Per the
  user's decision the generator is internal operations material. See "Open items": this contradicts
  `.agents/rules/docs.md` today and needs an explicit call before the generator is written.
- Notebook derivation (jupytext) and executing examples during the build. Executing anything needs
  extra dependencies in `docs/` (`numpy`, `Pillow`) plus a token for the live snippets; it is a
  separate step with its own plan.
- Pages beyond the landing page and the examples: `docs/content/api/` and `docs/content/notebooks/`
  stay empty, and the internal contract is not published at all — it sits at `docs/data-layout.md`,
  outside the content root.

## Hand steps (blocking; this order)

1. **Organization check.** Organization → Settings → Pages: confirm publishing is permitted for the
   organization. A public repository on the Free plan is allowed; this is only to rule out an
   organization-level restriction.
2. **Repo → Settings → Pages → Build and deployment → Source: GitHub Actions.**
3. **Repo → Settings → Pages → Custom domain:** `docs.global-wheat.com` → Save. This must happen
   **before** the DNS record exists.
4. **Verify the domain — optional, and check before doing it.** Organization → Settings → Pages →
   *Verified domains*: verifying `global-wheat.com` already protects its immediate subdomains (GitHub's
   own example: verifying `github.com` protects `docs.github.com`), so if it is listed there, nothing
   is needed. Otherwise the `_github-pages-challenge-…` `TXT` record is the only thing that stops
   someone attaching `docs.*` to their own Pages site after ours is gone. Skipping it is defensible
   while the site is live, and makes §6.4's "remove the `docs` `CNAME`" mandatory rather than tidy.
5. **お名前.com** (the panel described in `deploy/README.md` §2.2, which also carries the warning
   that the ホスト名 field takes the label only and appends `.global-wheat.com`):
   - `CNAME`: ホスト名 `docs`, VALUE `utokyo-fieldphenomics-lab.github.io` with **no trailing dot**,
     TTL 3600 — no `A` records, which are the apex pattern and are not used for a subdomain. The value
     is proven: the org already points `www.global-rice.com` at the same name.
   - `TXT`: only if step 4 was needed, and both strings trimmed.
6. **After DNS resolves:** Repo → Settings → Pages → **Enforce HTTPS**. The checkbox appears only
   once the certificate is issued, which can take up to 24 hours.

## Phase 1 — pipeline proof

Deliverables:

1. **new `zensical.toml`** at the repository root. Generate it with
   `uv run --project docs zensical new` in a scratch directory (never in the checkout: it writes a
   `docs/` of its own), copy the file out, then edit only:
   - `site_url = "https://docs.global-wheat.com/"` (trailing slash),
   - `site_name`, `site_description`,
   - `docs_dir = "docs"`, `site_dir = "site"` (add explicitly with a one-line comment saying they are
     relative to this file, so nobody moves the config into `docs/` later),
   - `repo_url`, `repo_name = "UTokyo-FieldPhenomics-Lab/gwhs-biohackathon-api-gateway"`,
     `edit_uri = "edit/main/docs/"`,
   - `nav` = `Start here` (`index.md`), `Examples` (`examples/index.md`), `Interactive API reference`
     (`https://api.global-wheat.com/docs`). **Corrected after review:** a `Data layout` entry pointed
     at the internal contract, which lists index tables and the object-key layout; it moved out of the
     content root on 2026-10-15 and participants read the response shapes from the interactive
     reference instead.
   Keep the generated `[project.theme]` and `[project.markdown_extensions]` blocks as they are:
   they are the upstream defaults and inventing a subset has no benefit.
   If the external URL in `nav` warns under `--strict`, drop that entry and link Swagger from
   `index.md` instead.
2. **new `docs/index.md`.** Short landing page: what the API gives a participant, the two calls
   (fetch an image, get a model result), where images resolve (`data.global-wheat.com`), and links to
   the interactive reference, the data layout, and the examples. Every claim must come from
   `README.md` or the interactive reference; invent no numbers and no process. How a participant receives
   a token is **not** documented anywhere yet, so link to `README.md` and record the gap in the plan's
   Result rather than authoring a process.
3. **new `.github/workflows/docs.yml`.** Shape, with the major versions confirmed on the marketplace
   before committing (this repo pins none yet):

   ```yaml
   name: docs

   on:
     push:
       branches: [main]
       paths: ["docs/**", "zensical.toml", ".github/workflows/docs.yml"]
     pull_request:
       paths: ["docs/**", "zensical.toml", ".github/workflows/docs.yml"]
     workflow_dispatch:

   permissions:
     contents: read
     pages: write
     id-token: write

   concurrency:
     group: pages
     cancel-in-progress: false

   jobs:
     build:
       runs-on: ubuntu-latest
       steps:
         - uses: actions/checkout@v4
         - uses: astral-sh/setup-uv@v5
           with:
             enable-cache: true
         - run: uv sync --locked --project docs
         - run: uv run --project docs zensical build --strict
         - uses: actions/configure-pages@v5
         - uses: actions/upload-pages-artifact@v3
           with:
             path: site
     deploy:
       if: github.ref == 'refs/heads/main'
       needs: build
       runs-on: ubuntu-latest
       environment:
         name: github-pages
         url: ${{ steps.deployment.outputs.page_url }}
       steps:
         - id: deployment
           uses: actions/deploy-pages@v4
   ```

   A pull request therefore builds (and fails on a broken page) without deploying.

Local verification before pushing:

```bash
uv sync --locked --project docs
uv run --project docs zensical build --strict      # exit 0, site/ produced
uv run --project docs zensical serve               # http://localhost:8000, click the nav
git status --porcelain                             # empty: site/ is ignored
```

## Phase 2 — content, cross-linking, and the documents

- `README.md`: **done** — the "Documentation site" section carries the URL, the three commands run
  from the repository root, and the invariant that keeps `site/assets/` in the output; the layout tree
  gained `zensical.toml` and `docs/content/`.
- `AGENTS.md`: **done** — the tree and the stack line.
- **Cross-linking.** `docs/index.md` links to Swagger. The other direction is **done on 2026-10-15**:
  the `description` of `create_app` (`gateway/src/biohack_gateway/app.py`) now ends with
  "Guides and examples: https://docs.global-wheat.com", which is additive metadata in `openapi.json`
  and touches no endpoint. The repository's homepage field (currently `https://api.global-wheat.com/docs`)
  **still open** — a repository setting, not a file, so it needs the About panel or
  `gh repo edit --homepage`; `gh` is not installed on this workstation today.
- `deploy/README.md`: **written on 2026-10-15** — new §6 "The documentation site" carries the hand
  steps above, the CNAME value as it must be entered, the verification commands, and the disposal note
  below. This is the same class of resource as §2's bucket, distribution, and certificate: created by
  hand, once.
- `.agents/rules/docs.md`: **written on 2026-10-15** — hosting is GitHub Pages at
  `docs.global-wheat.com`, published from an Actions artifact on `main` with `--strict`; the content
  root holds site material only, because a virtualenv under it silently drops `site/assets/`; and
  disabling the site means removing the `docs` CNAME as well, or keeping the domain verified, because
  an unverified `CNAME` pointing at GitHub Pages with no site behind it is a takeover path.
- `.agents/references/2026101516_docs-site-hosting.md`: **written on 2026-10-15** — decisions and the
  measurements behind them, the missing-stylesheet failure with the six probes that isolated it, the
  post-fix verification, and the lessons.

## Verification (record the output in the reference file)

```bash
curl -sI https://docs.global-wheat.com/ | head -3
curl -s 'https://dns.google/resolve?name=docs.global-wheat.com&type=CNAME'
curl -sI http://docs.global-wheat.com/ | head -3
curl -sI https://utokyo-fieldphenomics-lab.github.io/gwhs-biohackathon-api-gateway/ | head -3
curl -s https://docs.global-wheat.com/ | grep -o '<title>[^<]*</title>'
```

Plus: the certificate issuer and the lock in the browser, and one push that changes a visible string
to confirm the redeploy.

## Rollback and disposal

The site is disposable by rule. Disabling it is Settings → Pages → Unpublish; **then** remove the
`docs` CNAME at お名前.com, or leave the domain verified with the TXT record from hand step 4, so the
name cannot be claimed by someone else. Reverting the plan's files removes the site's content and
nothing else depends on it.

## Result (2026-10-15)

Live at <https://docs.global-wheat.com>. Both workflow runs are green
(`38697853053`, `38698235228`); the second published the complete site after the fix below.

| Check | Result |
|---|---|
| `uv run --project docs zensical build --strict`, from the repository root | exit 0, 8 asset files, `git status --porcelain` empty |
| `https://docs.global-wheat.com/` | `200`, `server: GitHub.com`, certificate `CN=docs.global-wheat.com` |
| The three asset URLs the page references | all `200` |
| Rendered page | nav, tables, code blocks with copy buttons, search, `On this page` all correct |
| `dns.google` CNAME for `docs.global-wheat.com` | `utokyo-fieldphenomics-lab.github.io` |

One failure on the way: the first deploy published every page and no `site/assets/`, so the site was
unstyled, and `--strict` reported nothing. A virtualenv under `docs_dir` — `uv sync --project docs`
creates `docs/.venv` — stops the theme assets being written; the pages moved to `docs/content/` and
`docs_dir` follows. See the reference file for the probes that isolated it.

Still open when this was written: `Enforce HTTPS` was unticked (ticked on 2026-10-15 afterwards), the
repository homepage still pointed at `api.*/docs`, and a participant's token hand-out is still
undocumented beyond "ask the organisers". After a content review the same day, the internal contract
left the site and the landing page was rewritten around what a participant needs: the calls, the
responses, the addressing rules, and the datasets — no index tables, no object-key layout, and no
development-only dataset.

## Open items

- `.agents/rules/docs.md` still says the API reference is generated markdown committed under
  `docs/content/api/` and rendered by the site, and that `docs/` only renders it. The user's decision
  that `gen_api_docs.py` is internal operations material contracts that. The rule is left unchanged
  here rather than silently rewritten: it needs an explicit call before the generator is written.
- How a participant receives a token is not written down anywhere. **Deferred by decision on
  2026-10-15**: it belongs in the docs later, in the form "ask the organisers by email", and until
  then the landing page links to `README.md` and states no process of its own. Nothing blocks the
  launch on this.

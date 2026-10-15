# The documentation site: GitHub Pages, `docs.global-wheat.com`, and the missing stylesheet

2026-10-15 16:00 JST. Hosting decision, bring-up, and the one failure that cost most of the session.
The plan is [`../plans/2026101521_docs-site-hosting.md`](../plans/2026101521_docs-site-hosting.md);
the runbook the operator follows is [`../../deploy/README.md`](../../deploy/README.md) §6.

## Objective

One URL a participant can be given before the event, serving curated documentation, independent of
the gateway host, which is on demand and whose Elastic IP one measured network already drops
([`2026101513_campus-path-blocks-the-eip.md`](2026101513_campus-path-blocks-the-eip.md)).

## Decisions, and what each rests on

| Decision | Evidence |
|---|---|
| GitHub Pages, not CloudFront and not a route on the gateway | The repository is public, so Pages needs no plan change; `site/` is rewritten on every push while `data.*` objects are write-once and never invalidated, so the two must not share a distribution; and the site has to outlive a stopped host |
| Custom domain `docs.global-wheat.com` | One hand record in the panel that already carries `api` and `data`, and a URL worth printing. This zone already pointed `www.global-wheat.com` at `utokyo-fieldphenomics-lab.github.io` for another repository's site, so the CNAME target was proven here rather than inferred |
| Publishing source is an Actions artifact | No `CNAME` file is needed (GitHub ignores one for workflow deployments), Jekyll never runs, and the build holds no secret |
| Verifying the custom domain is optional | GitHub verifies subdomains with the apex: verifying `global-wheat.com` would already cover `docs.*`. Public DNS showed no `_github-pages-challenge-…` record, so the zone is unverified today; the runbook states the price of skipping it — the `docs` CNAME must be removed when the site is retired |
| `api.*/docs` stays FastAPI's Swagger | It is an existing published surface and an open path (`gateway/src/biohack_gateway/app.py:50`); the curated site links to it instead of duplicating it |

## What was done

1. `zensical.toml` at the repository root, from `zensical new` in a scratch directory with only the
   project fields edited. The nav links Swagger as an external URL, which `--strict` accepts.
2. `docs/index.md` — the landing page: what answers today, how to get a token, the calls, the
   datasets. Every claim is from `README.md` or the live API; the token process is
   deliberately not invented.
3. `.github/workflows/docs.yml`: `uv sync --locked --project docs`, `zensical build --strict`,
   `upload-pages-artifact`, `deploy-pages`; pull requests build without deploying. Verified action
   majors against the marketplace on the day: `checkout@v7`, `setup-uv@v10.3.0` (no moving major tag
   exists, so the version is exact), `configure-pages@v6`, `upload-pages-artifact@v5`,
   `deploy-pages@v5`.
4. Operator steps: Pages source set to GitHub Actions, custom domain saved **before** the DNS record,
   `CNAME docs → utokyo-fieldphenomics-lab.github.io` at お名前.com. DNS took a few minutes to appear
   on the authoritative servers: `dns.google` answered NXDOMAIN from `dnsv.jp` while the panel already
   showed the record, and the zone's negative TTL is 300 s, so a re-query settles it.

## The failure: a published site with no stylesheet

The first deploy was green and the live page answered `200` — with `site/assets/` absent, so every
page rendered unstyled. The HTML referenced `./assets/stylesheets/modern/main.e15858d4.min.css`; the
file was 404. `zensical build` reported `No issues found` and no warning.

**Cause: a virtualenv anywhere under `docs_dir` stops the theme assets being written.** `uv sync
--project docs` creates `docs/.venv`, and `docs_dir` was `docs`. Anything under the content root makes
the build skip the asset copy silently.

How it was isolated, in order:

| Probe | Result |
|---|---|
| `zs` build in the checkout | 8 files, no `assets/` |
| Same build in a `cp -a` copy of the checkout | assets present — which sent the investigation the wrong way for a while, because those copies had had `docs/.venv` removed or replaced |
| Fresh `git clone` from GitHub + `uv sync` + build | **reproduced**: 8 files, no assets — so it was the build, not the artifact upload |
| Full repo config + real content in a scratch project | assets present, so it was neither the config nor the pages |
| Scratch project **with `.venv` moved out of `docs/`** | assets present |
| Scratch project with content in `docs/content/` and the venv left in `docs/` | assets present |

The fix: the pages moved to `docs/content/`, `docs_dir = "docs/content"`, `edit_uri` follows, and
`docs/` keeps only the subproject's `pyproject.toml`, `uv.lock`, and `.venv`. The `exclude` plugin does
**not** work around it: `"docs/.venv/**"` in its globs left the output exactly as broken, which is why
the fix is structural. The invariant is recorded in [`../rules/docs.md`](../rules/docs.md).

## Verified after the fix

| Check | Result |
|---|---|
| `uv run --project docs zensical build --strict` (repo root) | exit 0, 8 asset files, `git status --porcelain` clean (`/site` is ignored) |
| Workflow runs `38697853053` (before the fix) and `38698235228` | both green; the second published the complete site |
| `curl -sI https://docs.global-wheat.com/` | `200`, `server: GitHub.com` |
| Assets over the live URL | `bundle.8b1c90bd.min.js`, `main.e15858d4.min.css`, `palette.b27955a4.min.css` all `200` |
| Certificate | `subject=CN=docs.global-wheat.com` — GitHub issued it within the hour, so the wildcard `*.github.io` state of the first attempt is gone |
| Rendered page | header, nav (`Start here`, `Data layout`, `Examples`, `Interactive API reference`), tables, copy buttons, search all correct |

## Lessons

- **A green build is not a styled build.** `--strict` catches broken links, not a missing stylesheet.
  The check that would have caught this in one step is fetching one asset URL from the deployed site,
  which is now part of `deploy/README.md` §6.3.
- **`Connection refused`-style ambiguity exists in builds too.** `cp -a` copies are not clean-room
  reproductions: they carry ignored directories, and the first half of this investigation compared
  builds that differed by a `.venv`. A fresh `git clone` is the only faithful CI reproduction.
- **The neighbouring site in this zone has the other failure mode, unfixed:** `https://www.global-wheat.com/`
  is served with GitHub's wildcard certificate, so every HTTPS client fails there while `http://`
  returns `200`. A resolving `CNAME` says nothing about a certificate. Worth a one-line repair for
  whoever owns that repository.
- **A tool's own cache and environment belong outside the directory it publishes.** The content root
  is an output-side interface, and the natural place to put a `.venv` is exactly the wrong one.

## Remaining

- `Enforce HTTPS` is still unticked (`http://docs.global-wheat.com/` answers `200` rather than a
  redirect), and the certificate is now issued, so the checkbox is available.
- The repository homepage still points at `https://api.global-wheat.com/docs`; the plan's Phase 2
  changes it to `docs.*`. That is a repository setting, not a file.
- The cross-link the other way — one line in `create_app`'s `description` pointing at
  `https://docs.global-wheat.com` — is not written yet.
- How a participant receives a token is still undocumented; the landing page says to ask the
  organisers, which is the decision, and nothing more specific has been agreed.

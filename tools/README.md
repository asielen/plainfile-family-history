# tools/

The `fha` command suite lives here. Run via `python tools/fha.py <command>` from the repo
root, or `python tools/<tool>.py` for standalone use.

These tools are **generic**: they operate on any spec-conforming archive and contain no family data.
They are the "replaceable glue" of the philosophy — disposable, regenerable from the spec, and safe to publish.
`TOOLING.md` (repo root) is the design document for every tool; consult it before changing any behavior.

## Implemented tools (milestone 2)

| Tool | File | Status |
|---|---|---|
| `fha views timeline` | `views.py` | ✓ per-person and --all-curated |
| `fha views sources-index` | `views.py` | ✓ per-person, --all-curated, --couple-folders |
| `fha views draft-queue` | `views.py` | ✓ per-person and --all-curated |
| `fha views brackets` | `views.py` | ✓ W103 bracket refresh, W110 Ahnentafel placement; `--fix` applies, `--dry-run` previews |
| `fha views tree` | `views.py` | ✓ ancestors/descendants/fan modes; `--format json\|dot`; `--generations N`; `--out FILE`; `--format html` deferred (D6) |
| `fha doctor` | `doctor.py` | ✓ all 11 checks; D5 applied (absent index/photoindex = warning, not error) |
| `fha find <ID>` | `find.py` | ✓ P/S/C/L/H id types; structured index path when present; tree-scan fallback when absent |
| `fha find --text "…"` | `find.py` | ✓ notes_fts + re.search; photo captions searched when photoindex is fresh (else skip-note); `transcripts_fts` created but not yet populated — transcript search deferred (D7) |
| `fha find --related <ID>` | `find.py` | ⚑ deferred to BUILD.md M4.3 (D4); prints deferral message, exits 0 |
| `fha id check <ID>` | `fha.py` alias | ✓ re-routed through `find.find_by_id` in fha.py dispatcher |

Views require a fresh `.cache/index.sqlite` (run `fha index` first). `fha find` uses the index when present, warns when it is stale, and falls back to a tree scan only when the index is absent or unreadable; `fha doctor` degrades gracefully without caches.
Generated files carry the `<!-- GENERATED … -->` header and must not be hand-edited.

## Implemented tools (milestone 3, in progress)

| Tool | File | Status |
|---|---|---|
| `fha photoindex [--full]` | `photoindex.py` | ✓ M3.1 — schema, exiftool scan (incremental by mtime/size; `--full` rescans all), variation grouping, person resolution |
| `fha photoindex find` | `photoindex.py` | ✓ M3.2 — `--person`/`--keyword`/`--edtf`/`--text` filters (AND'd when combined); one path per group by default, `--files` for raw rows. The triage/reconcile/tag-person/report sub-commands (M3.3–M3.4) remain ⚑ deferred to follow-up PRs |

## fha photoindex — implementation status

| Feature | Status | Notes |
|---|---|---|
| Schema (`.cache/photos.sqlite`) | ✓ | `photos`, `photo_groups`, `photo_keywords`, `photo_face_regions`, `photo_people`, `photo_fts`; face regions cache XMP names/types/area JSON so weak person resolution can be rebuilt without re-scraping unchanged images |
| Scan — incremental | ✓ | Skips re-scraping a file via exiftool when `(path, mtime, size)` is unchanged; removes cache rows for files no longer on disk. Existing compatible caches without `photo_face_regions` get one backfill scrape; incompatible/corrupt disposable caches are recreated |
| Scan — `--full` | ✓ | Bypasses the incremental check, rescans every file |
| Variation grouping | ✓ | Pass 1: shared `SOURCE:` keyword. Pass 2: same directory + same filename `base_id` (`_lib.parse_media_filename`). `is_primary`, `variant_copy`, `variant_role` populated; grouping is recomputed in full on every scan |
| Date resolution (`edtf_resolved`, `date_conflict`) | ✓ | Best-confidence variant wins ties broken by the group's primary file, then by path; non-overlapping bounds across variants set `date_conflict=1` |
| Person resolution | ✓ | Rebuilt every scan from cached `photo_keywords` + `photo_face_regions`: `pid-keyword` (regex-only, no index needed) → `face-tag` (exact match against `person_face_tags`, skipped if ambiguous) → `name-match`. The latter two require a fresh `.cache/index.sqlite`; absent/stale/unreadable index degrades to pid-keyword only |
| `fha photoindex find` | ✓ (BUILD.md M3.2) | `--person P-id`, `--keyword TERM` (case-insensitive substring), `--edtf EDTF` (bounds-overlap against each photo's own `edtf`), `--text "…"` (`photo_fts`); filters AND together. Default dedupes matches to one row per group (`primary_path`); `--files` returns every matching raw row. Absent/unreadable `.cache/photos.sqlite` → clear error, exit 3; stale → warns but still queries |
| `fha photoindex triage` + `report` | ⚑ deferred (BUILD.md M3.3) | CLI stub registered (prints "deferred to a follow-up photoindex PR", exits 0) so the command tree is coherent |
| `fha photoindex reconcile` + `tag-person` | ⚑ deferred (BUILD.md M3.4) | Same CLI-stub treatment |

Test fixture: `tests/fixtures/photo-fixture/` — 4 placeholder JPEGs with real embedded metadata (written via exiftool, not a code-level stub): a front/back variation pair with disagreeing `DATE:` keywords (exercises `date_conflict`), a photo carrying a `SOURCE:` keyword (exercises source-id grouping), and one ungrouped photo.

Automated tests: `tests/test_photoindex.py` (stdlib `unittest`, no new dependency) monkeypatches `photoindex._run_exiftool` to inject canned JSON rows over a copy of the fixture, covering grouping/date-conflict/pid-keyword resolution, face-region caching, stale-index-disables-weak-resolution behavior, and fresh-index weak-resolution refresh from cached regions. Run with `python -m unittest tests.test_photoindex -v` from the repo root. This is the first `.py` test file in the repo; no test runner is wired into CI yet.

## fha doctor — implementation status

| Check | Status | Notes |
|---|---|---|
| Archive root + fha.yaml | ✓ | Fatal exit 2 if either absent/unparseable |
| Mapped roots reachable | ✓ | ✓/✗ per root; unreachable → exit 2 |
| exiftool on PATH | ✓ | ✗ → exit 1 (warning; not a hard dep for most commands) |
| Python deps (PyYAML) | ✓ | ✗ → exit 2 |
| Index freshness | ✓ | absent/stale → exit 1 (D5) |
| Photoindex freshness | ✓ | schema probed before "fresh"; absent/stale/unreadable → exit 1 (D5) |
| Lint summary | ✓ | import-and-call `run_lint_silent`; E-level findings → exit 2 |
| Inbox aging (14 days) | ✓ | printed only when inbox/ dir exists |
| Counts | ✓ | from index when fresh, else quick scan |
| E018 findings detail | ✓ | lists findings when present |
| Backup reminder | ✓ | always printed |

## fha find — implementation status

| Flag / feature | Status | Notes |
|---|---|---|
| `<P-id>` lookup | ✓ | file, couple folder, companions, claims, citations, photo note |
| `<S-id>` lookup | ✓ | record, files (resolved + on-disk status), claims, citation sites |
| `<C-id>` lookup | ✓ | source record + approx line, status, value, corroborates/contradicts |
| `<L-id>` lookup | ✓ | place entry, claims referencing it, prose mentions |
| `<H-id>` lookup | ✓ | hypothesis entry, section heading, status, prose mentions |
| `--text "…"` | ✓ | notes_fts + re.search; photo captions searched when photoindex is fresh, else explicit skip-note; transcript FTS ⚑ deferred (D7) |
| `--related <ID>` | ⚑ deferred | prints deferral message, exits 0 (D4) |
| Index fallback | ✓ | stale index warns but remains structured; absent/unreadable index tree-scans with "WARNING: index not fresh" header |

## Implemented tools (milestone 1)

| Tool | File | Status |
|---|---|---|
| Shared library | `_lib.py` | ✓ foundations |
| `fha` CLI dispatcher | `fha.py` | ✓ routes all subcommands |
| `fha id mint/check` | `id.py` | ✓ Crockford Base32, existence check |
| `fha index` | `index.py` | ✓ full SQLite rebuild + incremental upsert |
| `fha lint` | `lint.py` | ✓ see lint status table below |
| `fha stubs` | `stubs.py` | ✓ scan + mint stubs |

`fha lint --root example-archive` exits 1 with one expected W101 — the fictional Thomas Hartley
has no located death record, which is intentional for a minimal fixture.
No E-level errors. The `example-archive/` is a demonstration fixture permitted to carry documented
known warnings; the `tests/fixtures/` clean fixture (not yet built) must exit 0.

## fha lint — implementation status

This table is the authoritative build-status record for lint codes and flags.
`TOOLING.md §3` describes the full design intent; this table tracks what is actually built.
A code listed in TOOLING must appear here as either ✓ or ⚑ before the tool is milestone-complete.

| Code / flag | Status | Notes |
|-------------|--------|-------|
| E001 – E010, E013 – E017 | ✓ implemented | — |
| E011 | ✓ implemented | inventory→disk direction; document disk→inventory scan by filename S-id. Photo disk→inventory direction requires `--with-exif`. |
| E012 | ✓ implemented | Only runs when `--with-exif` is passed (requires exiftool on PATH); silently skipped otherwise. |
| E018 | ✓ implemented (partial) | Deprecated-command check active. Photo-rename instruction check is a no-op pass — text pattern too ambiguous to assert direction reliably. |
| W101, W102, W104, W106, W107, W108, W109 | ✓ implemented | — |
| W103 | ✓ implemented | Stale couple-folder bracket lists; fires in `fha lint` and `fha views brackets`. |
| W105 | ⚑ deferred | Requires mtime comparison against a known-good generated state. |
| W110 | ✓ implemented | Direct-line person file in wrong Ahnentafel couple folder; fires in `fha lint` (requires `root_person`) and `fha views brackets`. |
| `--with-exif` | ✓ implemented | Exiftool batch keyword read; drives E012 and photo-side E011. |
| `--json` | ✓ implemented | — |
| `--format-check` | ✓ implemented (partial) | Final-newline and CRLF hygiene active. Frontmatter key order, lowercase ID normalization, YAML indentation: ⚑ deferred. |
| `--format-write` | ✓ implemented (partial) | Writes the fixes reported by `--format-check`. Frontmatter normalization: ⚑ deferred. |
| `--dry-run` | ✓ implemented | Each active fix mode prints "Would …" lines without writing. |
| `--mint-stubs` | ✓ implemented | — |
| `--spawn-questions` | ✓ implemented | — |
| `--fix-inventory` | ⚠ CLI placeholder | Prints a not-yet-implemented warning; `fha process` is the current alternative. |

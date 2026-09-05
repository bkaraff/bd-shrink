# bd_shrink Implementation Plan

Status: implementation complete; final verification passed.

This file records the next work items so the project can be resumed after a
lost coding session.

## Implementation Progress

- TUI dependency reporting and Fedora guidance: complete.
- Trailing-slash and exact-ISO path handling: complete.
- ISO staging and successful-cleanup behavior: complete.
- Default orphan exclusion with `--preserve-orphans` opt-in: complete.
- Over-target failure before ISO/burn: complete.
- Regression tests: complete (`319 passed`).
- Ruff check and format check: passed.
- Python compile check: passed.
- Review follow-up: fixed ISO destination threading so staging never changes the requested ISO path.
- Review follow-up: `-o name.iso` without `--iso` now fails validation instead of silently creating a folder.
- Fedora interpreter selection: the shim now prefers `/usr/bin/python3` when DNF-installed TUI modules are available, while retaining a fallback to `python3`.
- Exact source dry-run now reports the source-named output and matching ISO path.
- `shellcheck` could not run because it is not installed in this environment.

## Confirmed Baseline

- Branch: `dev-next`
- HEAD: `b6f50d2`
- Worktree was clean when this plan was created.
- Baseline tests: `309 passed`.
- The real test source was:
  `/mnt/downloads/Yellow Dragon's Village (2021) & Visitors (2023) 1080p USA Blu-ray AVC DTS-HD MA 5.1/`
- The attempted output parent was `/data-ssd/Videos/`.

## Confirmed Problems

### 1. TUI Python dependency failure

Command-line TUI startup failed with:

```text
No module named 'wcwidth'
```

The current error handler reports this inaccurately as missing both
`questionary` and `rich`. The project metadata declares `questionary` and
`rich`, but `--install-deps` only checks external binaries and gives no Fedora
Python package guidance.

Dependencies are available from Fedora/DNF repositories and should not require
an ad hoc `pip install` workflow.

### 2. ISO path and output directory confusion

The successful run logged:

```text
ISO created: /data-ssd/Videos.iso
```

The ISO was written beside the resolved output directory, not inside it. A
trailing slash in `-o /data-ssd/Videos/` was lost during path normalization.
Because `/data-ssd/Videos/BDMV` existed, the path was treated as an exact BDMV
output instead of a parent directory for a source-named output.

### 3. Oversized surgical output

The run produced a 141.90 GB output against a 23 GB target. The checkpoint/log
reported:

- 8 inventoried clips
- 5 clips remuxed
- 3 clips copied
- output size: 141.90 GB

The surgical orphan pass copies every source `.m2ts` not already present. This
can copy large, unreferenced source clips and defeat the shrink target.

### 4. Oversized ISO is allowed

Validation only warns when the output exceeds the target, then ISO creation
continues. This can create a known-invalid oversized ISO instead of stopping
before the final artifact is produced.

## Implementation Tasks

### A. Repair dependency discovery and messaging

- Add explicit Python-module checks for the TUI runtime, including
  `questionary`, `rich`, and `wcwidth`.
- Add Fedora installation guidance using DNF packages, including the relevant
  `python3-*` packages.
- Keep binary dependency checks separate from Python-module checks.
- Change TUI import failure reporting to name the actual missing module and the
  matching DNF installation command.
- Ensure `--install-deps` reports both external tools and Python dependencies.
- Add unit tests for complete dependencies, missing `wcwidth`, and accurate
  error output.

### B. Make output and ISO paths unambiguous

- Preserve whether the user supplied a trailing path separator before calling
  `abspath`.
- Treat an explicitly trailing-slash output as a parent directory and create a
  source-named child even if a stale `BDMV/` exists there.
- Continue to support an exact output path ending in `.iso`.
- Resolve and display the final ISO path during dry-run.
- Add tests for parent-directory output, stale BDMV output, exact `.iso` paths,
  and dry-run path reporting.

### C. Prevent orphan clips from defeating shrinking

- Remove the unconditional copy-all-orphans behavior from the default
  surgical path.
- Retain clips referenced by the playlists and navigation structures that can
  be identified reliably.
- If broad orphan preservation remains useful, expose it as an explicit opt-in
  flag with a clear warning that it may exceed the target.
- Ensure `--no-extras` continues to exclude extras and orphans.
- Add tests with large unreferenced clips proving they are not copied by
  default.
- Update rebuild statistics and logging to distinguish referenced copies from
  optional orphan copies.

### D. Stop before producing an oversized final artifact

- Make an output that exceeds `--target` a pipeline failure before ISO creation
  or burning.
- Include actual size, target size, and likely causes in the error message.
- Preserve validation checkpoints and work data for diagnosis/resume.
- Add orchestration tests confirming ISO creation is not called when the size
  check fails.

### E. Use temporary staging for ISO-only output

- Build and validate the BDMV in a work/staging directory when only `--iso` is
  requested.
- Create the ISO from that staging directory.
- Remove staging only after successful ISO creation, unless the user requests
  work retention.
- Retain staging after failure so the run can be diagnosed or resumed.
- Keep folder-output behavior unchanged.
- Add tests for successful cleanup, failure retention, and ISO-plus-burn flow.

### F. Documentation and verification

- Update `README.md` with Fedora/DNF Python dependency installation.
- Document parent-directory versus exact `.iso` output semantics.
- Document that default surgical output does not copy arbitrary orphan clips.
- Document that an over-target output fails before ISO/burn.
- Update `AGENTS.md` with the final behavior and resume details.
- Run:

```bash
pytest -q
ruff check .
ruff format --check .
python -m compileall bd_shrink
bash -n bd_shrink.sh
shellcheck bd_shrink.sh
```

- Re-run the exact source command as a dry run and confirm the resolved output
  and ISO paths before starting another real encode.

## Important Design Decisions

- Default behavior must prioritize fitting the requested target over preserving
  arbitrary unreferenced source content.
- `--iso` means the final user-facing artifact is the ISO; intermediate BDMV
  staging should not remain as an unexpected large output directory after a
  successful run.
- Failure diagnostics and resumability take priority over aggressive cleanup.
- The implementation should use Fedora packages for the supported TUI runtime;
  pip-only instructions are not the primary fix.

## Suggested Execution Order

1. Add dependency checks and tests.
2. Fix output-path resolution and dry-run reporting.
3. Fix surgical clip selection and add size-failure behavior.
4. Add ISO staging/cleanup behavior.
5. Update documentation.
6. Run the full verification suite.
7. Perform a dry run, then resume with a real encode only after the dry-run
   paths and target behavior are confirmed.

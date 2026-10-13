# Releases

One version number lives in three files and a test keeps them equal: `package.json` (npm), `pyproject.toml` (the Python package) and
`CITATION.cff`. The git tag is `v<version>`.

| channel | name | how it ships | status |
|---|---|---|---|
| npm | [`ants-lab`](https://www.npmjs.com/package/ants-lab) | `npm publish` from a tagged commit | the registry shows the latest: `npm view ants-lab version` |
| GitHub | [p-to-q/ants](https://github.com/p-to-q/ants) | the tag and its release notes | public |
| PyPI | `ants-lab` | not published | planned |

## Cut a release

1. Make `main` green: `pytest tests -q -m "not slow"` and the slow chaos suite (`-m slow`), and the CI run on the commit.
2. Bump the version in the three files (a test fails if they differ). For a pre-release use `0.2.0-rc.1` style.
3. Add a dated entry to [changes.md](changes.md): what changed, why, and what it forecloses.
4. `npm pack --dry-run` and read the file list. The package ships `bin/`, `src/`, `contracts/`, the data catalog files, `docs/`,
   `assets/` and the licence files; it must not ship `internal/`, `runs/`, raw data or bytecode.
5. Install the tarball into an empty directory and run it:
   ```bash
   npm pack --pack-destination /tmp/ants-pack && cd /tmp && npm install --prefix t ./ants-pack/ants-lab-*.tgz
   ANTS_HOME=/tmp/ants-home t/node_modules/.bin/ants --version
   ```
6. Tag and push: `git tag v<version> && git push origin v<version>`. The `release` workflow checks that the tag matches the three files.
7. `npm publish`. The publishing account has staged publishing on: the command queues the version and it goes live when the account
   owner approves it on npmjs.com. Until then the registry shows a `0.0.0-stage` placeholder.
8. Verify from the registry: `npx ants-lab@<version> --version`.

## Why a launcher

The npm package contains no compiled code. `bin/ants.js` finds Python 3.11+, builds a virtual environment in `~/.ants` the first
time, installs the bundled source into it, and forwards every argument and signal to the `ants` command. It sets `ANTS_ROOT` to the
package directory so bundled contracts resolve by name (`ants run --contract tinyts_synth_hard`). A new package version builds a new
environment next to the old one; delete `~/.ants/venv-<old version>` to reclaim the space.

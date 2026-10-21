# MCPMark filesystem: direct MCP vs MIP gateway

Upstream: https://github.com/eval-sys/mcpmark, pinned at commit
`cd45b7f57923b9b3985467f5139927575f83141c` (2026-06-12), cloned into `upstream/`.

Task set: all 40 filesystem tasks (`upstream/tasks/filesystem/{easy,standard}/<category>/<task>/`,
10 easy and 30 standard). Each task has `description.md`, `meta.json` and `verify.py`. The initial state
is the per-category zip MCPMark downloads (`https://storage.mcpmark.ai/filesystem/<category>.zip`,
the URL map is in `upstream/src/mcp_services/filesystem/filesystem_state_manager.py`), unpacked into `fixtures/`.

## Files

- `run.py`: runs (task, arm, trial). Each run copies `fixtures/<category>` to `runs/<key>/<category>`
  with mtimes kept (`shutil.copy2`), starts its own CLI and MCP server, then scores the result with the
  task's `verify.py`. One JSON line per run is appended to `runs/log.jsonl`.
- `report.py`: `runs/log.jsonl` to `results.json`, and prints the tables in RESULTS.md.
- `trace.py runs/<key>`: prints every tool call and reply of one run (from `cli.jsonl`).
- `snapshots/<arm>/`: a frozen copy of `gateway/mip_gateway.py`, `mip.py`, `fast.py`, `bench/tokens.py` with
  `SHA256SUMS`. MIP arms run from this copy, never the live `gateway/`.
- `runs/excluded.jsonl`: runs set aside because the gateway changed under them (see RESULTS.md).

## Reproduce

```bash
uv venv --python 3.12 bench/mcpmark/.venv
```

```bash
export MCPMARK_LINK=/tmp/mcpmark_runs   # any path WITHOUT spaces; a symlink to runs/ is made there
```

```bash
python3 bench/mcpmark/run.py --arms direct --trials 3 --jobs 4
```

```bash
python3 bench/mcpmark/run.py --snapshot mip_v2
```

```bash
python3 bench/mcpmark/run.py --arms mip_v2 --system bench/mcpmark/system_mip_v2.txt --trials 3 --jobs 4
```

```bash
python3 bench/mcpmark/report.py
```

`verify.py` is stdlib-only; it runs under the uv Python 3.12 venv as `python verify.py` with
`FILESYSTEM_TEST_DIR=<run dir>/<category>`, which is what MCPMark's `FilesystemTaskManager.run_verification`
does. Exit code 0 means pass.

## How each run is invoked

```
claude -p --model claude-haiku-5-5 --tools "" --strict-mcp-config --mcp-config <cfg>
  --allowedTools <mcp__fs__* | mcp__mip__run> --permission-mode bypassPermissions --setting-sources ""
  --output-format stream-json --verbose --no-session-persistence --system-prompt <SYSTEM> "<task text>"
```

Task text = `description.md` + MCPMark's own suffix ("Note: Based on your understanding, solve the task all
at once by yourself, don't ask for my opinions on anything.") + `The directory for this task is <abs path>`.
The last line is ours, sent to both arms: the MIP gateway only allows writes under directories typed in the
intent, so the path has to be in the text. MCPMark itself does not state the path; the model finds it with
`list_allowed_directories`.

The path given is under `$MCPMARK_LINK`, not the real `runs/` path: this checkout sits under
`.../Application Support/...`, and the gateway splits the intent on whitespace, so a path with a space can
never be named (a gateway limitation, recorded in RESULTS.md).

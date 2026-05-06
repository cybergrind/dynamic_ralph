# coworker_llm backends

`coworker_llm` ships three CLIs (`/ask-llm`, `/llm-write`, `/extract-chat`)
that delegate work to a configurable agent runner. The runner is called
the *backend*; everything below describes how to pick one, how to
configure it, and how to add a new one.

## Selecting a backend

Priority order (first match wins):

1. `--backend <name>` flag on any CLI (or on `scripts/coworker_smoke.py`).
2. `COWORKER_BACKEND` environment variable.
3. Default: `opencode`.

`uv run scripts/coworker_smoke.py --list-backends` shows registered
backend names along with whether the underlying CLI is on `PATH`.

## Backends

### opencode

Wraps the `opencode run` CLI. Reads are passed via `-f`; writes are
constrained by `--dir`.

| Env var | Default | Purpose |
| --- | --- | --- |
| `COWORKER_OPENCODE_BIN` | `opencode` | Path to the binary |

### claude-code

Runs `claude -p` non-interactively. Read/write permissions come from
`--add-dir` (one entry per allowed directory) plus `--allowedTools`
(default: `Read Write Edit MultiEdit Glob Grep`). The process is
launched with `cwd=writes_dir` so the agent's relative paths land in the
right place.

| Env var | Default | Purpose |
| --- | --- | --- |
| `COWORKER_CLAUDE_BIN` | `claude` | Path to the binary |
| `COWORKER_CLAUDE_MODEL` | `claude-haiku-4-5` | Model id passed via `--model` |
| `COWORKER_CLAUDE_UNRESTRICTED` | unset | Set to `1` to swap the explicit allowlist for `--dangerously-skip-permissions` |

### claude-api

**Placeholder.** Today shells out via `claude -p` exactly like
`claude-code`, but with its own config namespace and a more capable
default model. The intent is to swap the implementation to a direct
Anthropic SDK call once we're ready to take on that dependency; the
registry name and env-var contract are stable now to ease the future
migration.

| Env var | Default | Purpose |
| --- | --- | --- |
| `COWORKER_CLAUDE_API_BIN` | `claude` | Path to the binary |
| `COWORKER_CLAUDE_API_MODEL` | `claude-sonnet-4-6` | Model id passed via `--model` |
| `COWORKER_CLAUDE_API_UNRESTRICTED` | unset | Set to `1` to swap the explicit allowlist for `--dangerously-skip-permissions` |

## The protocol

Every backend implements a small typed protocol defined in
`coworker_llm/backend.py`:

```python
@dataclass(frozen=True)
class CoworkerRequest:
    prompt: str
    reads: tuple[str, ...] = ()       # files the agent must be allowed to read
    writes_dir: str | None = None     # directory the agent may write into
    expected_target: str | None = None  # informational

@dataclass(frozen=True)
class CoworkerResult:
    stdout: str
    extras: Mapping[str, str] = ...   # backend-specific telemetry; opaque to CLIs

class CoworkerBackend(Protocol):
    name: str
    def run(self, request: CoworkerRequest) -> CoworkerResult: ...
    def is_available(self) -> bool: ...
```

The fields are *intent-level*: callers say what files must be reachable
and where writes may land, not which CLI flags to use. Each backend
translates the intent into its own argv (or, for non-subprocess
backends, its own tool registrations).

Optional: a backend may expose `describe(request) -> list[str]` returning
the argv it would build. The smoke harness uses this for debugging; if
absent, the harness skips that line.

## Smoke harness

`scripts/coworker_smoke.py` runs canonical tasks through one or more
backends and prints what came back.

```bash
# Sanity-check one backend on the default task (roundtrip):
uv run scripts/coworker_smoke.py --backend opencode

# Compare two backends side-by-side:
uv run scripts/coworker_smoke.py --backend opencode --backend claude-code

# Pick a specific task; show argv but don't invoke:
uv run scripts/coworker_smoke.py --backend claude-code --task ask --dry-run

# Ad-hoc prompt:
uv run scripts/coworker_smoke.py --backend opencode \
    --prompt "list 3 fruits" --read /tmp/notes.txt
```

Tasks (`--task`):

- `ask` — read a fixture, find a sentinel token in its content. Reads only.
- `write` — given a context fixture, write a sentinel into a target file. Reads + writes_dir.
- `roundtrip` *(default)* — read + write + summarize. Exercises every field of `CoworkerRequest`.

Each task uses a fresh `tempfile.TemporaryDirectory()` and a randomized
sentinel so a stale cached response cannot satisfy the postcondition
check.

## Adding a new backend

1. Create `coworker_llm/backends/<your_backend>.py`. Provide a class with:
   - a string `name` attribute,
   - `from_env()` classmethod returning a configured instance,
   - `is_available() -> bool` (typically `shutil.which(self._binary) is not None`),
   - `run(request) -> CoworkerResult` translating intent → invocation,
   - optional `describe(request) -> list[str]` for the harness.
2. In `coworker_llm/backend.py` add a lazy factory function and an
   entry in `_BACKEND_FACTORIES`. Use a lazy import inside the factory
   so importing `coworker_llm.backend` doesn't pull in every backend's
   transitive deps.
3. Add unit tests in `tests/test_coworker_backend.py` patching
   `subprocess.run` (or the SDK call) to verify the produced argv /
   invocation shape.
4. Run the smoke harness against the new backend:
   `uv run scripts/coworker_smoke.py --backend <name>`.
5. The integration tests in `tests/test_coworker_llm_integration.py`
   pick the new backend up automatically once `is_available()` returns
   `True` on the dev box and `RUN_INTEGRATION=1` is set.

## Troubleshooting

**"unknown backend: …; available: …"** — the registry doesn't recognize
the name. Run `--list-backends` to see what's registered.

**`is_available()=False`** — the backend's CLI isn't on `PATH`. Set
`COWORKER_<NAME>_BIN` to the absolute path, or install the tool.

**Permission prompts when using `claude-code` in `-p` mode** — the
default `--allowedTools` allowlist covers Read/Write/Edit/MultiEdit/
Glob/Grep but not Bash. If the agent needs Bash, either add the tool
to a custom backend instance's `allowed_tools`, or set
`COWORKER_CLAUDE_UNRESTRICTED=1`.

**Target file not created** — the postcondition is checked in the CLI,
not the backend. The CLI's stderr message names the missing path; the
backend's stderr (if any) appeared in the captured output before that
message.

## Out of scope (deferred)

- Replacing `claude-api` with a direct Anthropic SDK implementation —
  would inline reads into the prompt and register a Write tool scoped
  to `writes_dir`. The registry slot and env-var contract are already
  in place; only the runtime needs to change.
- Pi-agent backend — pending confirmation of the pi CLI's read/write
  permission flags.
- Streaming output — `run()` returns a complete `CoworkerResult` today.
- Per-CLI default backend overrides (e.g. `COWORKER_BACKEND_ASK_LLM`).
- `extras` telemetry (model id, token counts, cost, duration) — the
  field is plumbed end-to-end, no backend currently populates it.

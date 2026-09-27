# jev-default-open

Context-aware Windows open routing powered by Jev. The repository currently
contains the architectural interfaces and TODO implementation shells.

## Architecture

- `src/jev_open/domain`: stable domain language and value objects.
- `src/jev_open/interception`: the Python side of the native hook seam.
- `src/jev_open/context`: IM context orchestration and provider adapters.
- `src/jev_open/actions`: Windows handler and application-profile discovery.
- `src/jev_open/decision`: Jev request construction and result policy.
- `src/jev_open/persistence`: encrypted state, preferences, history, and provenance.
- `src/jev_open/ui`: onboarding, tray, and confirmation-window interfaces.
- `native`: x64 hook DLL and native host definitions.
- `qq_bridge`: LiteLoaderQQNT bridge plugin definition.

See `docs/architecture.md` for module interfaces, invariants, and data flow.

## Development

Use Python 3.12 explicitly. The system Python may be a different version.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest
```

Native build prerequisites and bootstrap automation are TODO.

## Repository rules

- Never commit API keys, IM database keys, decrypted chat databases, or exports.
- Keep machine-local configuration outside the repository.
- `.env.example` contains variable names only.

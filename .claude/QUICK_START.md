# Quick start

```bash
uv sync                                # install deps + dev tools
uv run pytest -q                       # full suite
uv run pytest tests/test_patch.py -q   # one module
uv run ruff check .                    # lint
uv run ruff format .                   # format (CI runs --check)
uv run brimyr local                    # gate this branch vs the default branch
uv run brimyr version
uv run --group docs mkdocs serve       # docs preview on :8000
uv run --group docs mkdocs build       # render ./site (gitignored)
```

# Contributing to Securo

Thanks for your interest in contributing to Securo! This guide will help you get started.

## Getting Started

1. Fork the repository
2. Clone your fork: `git clone https://github.com/your-username/securo.git`
3. Start the stack: `docker compose up --build`
4. Open [http://localhost:3000](http://localhost:3000)

## Where to Start

New here? The smoothest first contribution is a small, self-contained one:

- Browse the [open issues](https://github.com/securo-finance/securo/issues), especially those labeled `good first issue` or `help wanted`, and pick something that already has a clear scope.
- Small bug fixes, docs improvements, and translation updates are always welcome and don't need any prior discussion, just open the PR.
- Comment on an issue to let others know you're picking it up, so two people don't work on the same thing.

Starting from an existing issue means the work is already something we want, so your PR has a clear path to being merged.

## Before Large or Core Changes

For anything bigger, a new feature, a refactor, or a change to a core mechanism (accounts, transactions, budgets, the rules engine, workspaces, sync, and similar), we'd love to talk it through **before** you write the code. It helps us confirm the idea fits the project's direction and that it's the right moment to build it, and it saves you from investing time in a PR that might not land.

Good ways to align first:

- Open a [feature request](.github/ISSUE_TEMPLATE/feature_request.md) describing what you'd like to build.
- Comment on the related issue if one already exists.
- Chat with us on [Discord](https://discord.gg/rUqTKtQ9S4).

Once there's a shared understanding, go ahead and build. Large PRs that arrive without any prior discussion are harder to review and sometimes don't align with where the project is heading, so a quick conversation up front is the best way to make your contribution count.

## Using AI

Use it. We do. Parts of this codebase were written with AI. This isn't a policy against the tools.

It's a policy about ownership. **We don't review the AI, we review you.** When a PR arrives, the questions are the same as they've always been: does this person understand what they're proposing, can they explain why it's built this way, and will they still be around if it breaks. Whatever produced the diff doesn't change any of that.

So whatever you use, before you open the PR:

- **You own the approach, not just the output.** You decided the strategy and delegated the typing. If the model picked the architecture and you went along with it, you don't know the change well enough to defend it in review.
- **You're the quality gate.** The change holds to the standards of the code already here: naming, structure, tests, error handling. AI writes plausible code, and plausible isn't the bar.
- **It fits where the product is going.** A change can work and still be wrong for Securo. Whether it belongs here is your call before it's ours.
- **You ran it.** Not "the tests should pass" — you ran them, you ran the app, you saw the change work.
- **The scope is what the issue asked for.** AI is generous with refactors nobody requested. Strip them. A thirty-file diff for a one-line bug goes back.
- **You're accountable after it merges.** If it breaks in three weeks, you're who we come to.

We won't ask which tools you used and we won't try to detect them. We'll read the code and ask questions. Contributors who understand their own work pass easily, and that was true long before any of this.

The same applies to issues. An issue produced by pointing a model at the repository and asking it to find problems is not a bug report. Tell us what you did, what happened, and what you expected.

## Development Workflow

1. Create a branch from `main`: `git checkout -b feature/your-feature`
2. Make your changes
3. Run backend tests: `cd backend && uv sync --all-extras && uv run pytest` (Python 3.11+)
4. Run frontend checks: `cd frontend && npm run lint && npm test`
5. Commit with a clear message (see below)
6. Push your branch and open a Pull Request

Optional but recommended, so you catch lint and type errors before CI does:

```bash
prek install                                   # once, from the repo root
# or, if you prefer the Python original:
pip install pre-commit && pre-commit install
```

This runs `ruff check` and `ty check` on the backend whenever you commit a
`backend/*.py` file. Both read their config from `backend/pyproject.toml`, so
local and CI stay in sync.

[prek](https://github.com/j178/prek) is a drop-in replacement for pre-commit:
same `.pre-commit-config.yaml`, but a single binary with no Python bootstrap.

### Frontend tests

Vitest and Testing Library. Render through `renderWithProviders` from
`@/test/utils`, which wires up TanStack Query, the router and i18n, and import
with the `@/` alias rather than a relative path. Assert on what the user sees:
the rendered text, the disabled button, the error that appears on a failed
request.

### Adding a frontend dependency

`frontend/.npmrc` never runs a package's install scripts, and asks npm to skip
releases younger than seven days so a compromised publish has time to be caught.
The cooldown needs npm 11.10 or newer; the npm that ships with Node 22 is older
and will ignore that line without saying so, so upgrade before you add anything:

```bash
npm install --global npm@latest
cd frontend && npm install <package>     # commit package.json and package-lock.json
```

If the package you want was published in the last week, npm resolves the release
before it. That is the point — wait, or say in the PR why you can't.
Either works.

## Commit Messages

Use clear, descriptive commit messages:

- `feat: add CSV export for transactions`
- `fix: correct balance calculation on account closure`
- `docs: update setup instructions`
- `refactor: simplify rule engine matching`

## Running Tests

```bash
# Backend tests (run from backend/, needs Python 3.11+; same as CI)
cd backend
uv sync --all-extras   # first time only — builds .venv from uv.lock, same versions as CI
source .venv/bin/activate
pytest

# No uv? pip works too, from an export of the lock:
#   pip install uv && uv export --frozen --all-extras --no-emit-project -o /tmp/req.txt
#   pip install --require-hashes -r /tmp/req.txt && pip install --no-deps -e .

# Backend tests with coverage
pytest --cov=app --cov-report=term-missing

# Backend lint + type check (same commands CI runs)
ruff check .
ty check .

# After changing dependencies in pyproject.toml: regenerate the lock and
# commit uv.lock along with it (CI enforces this)
./scripts/lock.sh

# After adding a migration: check the revision chain is still a single line
python3 scripts/check_migration_chain.py

# Frontend lint
cd frontend && npm run lint

# Frontend build check
cd frontend && npm run build
```

### Adding a migration

Number the file after the current head and chain it there, so
`backend/alembic/versions/` sorts in apply order:

```python
revision: str = "076"
down_revision: Union[str, None] = "075"
```

If another migration lands on `main` while your PR is open, your number is
taken and you have to renumber. CI catches this: the Migration Chain job runs
against your branch merged with `main`, so a clash fails there rather than on
someone's `alembic upgrade head` after both are merged.

## Pull Request Guidelines

- Keep PRs focused — one feature or fix per PR
- Include a clear description of what changed and why
- Make sure CI passes (tests + lint)
- Add tests for new backend functionality
- Update translations if adding user-facing strings (EN + PT-BR)

## Project Structure

```
backend/     → FastAPI + SQLAlchemy + Celery
frontend/    → React + TypeScript + Vite + Tailwind
docs/        → Design and implementation docs
scripts/     → Development utilities
```

## Reporting Issues

- Use the [bug report template](.github/ISSUE_TEMPLATE/bug_report.md) for bugs
- Use the [feature request template](.github/ISSUE_TEMPLATE/feature_request.md) for ideas
- Check existing issues before opening a new one

## License

By contributing, you agree that your contributions will be licensed under the [AGPL-3.0 License](LICENSE).

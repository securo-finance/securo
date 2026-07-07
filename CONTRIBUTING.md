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

## Development Workflow

1. Create a branch from `main`: `git checkout -b feature/your-feature`
2. Make your changes
3. Run backend tests: `cd backend && pip install -e ".[dev]" && pytest` (Python 3.11+)
4. Run frontend lint: `cd frontend && npm run lint`
5. Commit with a clear message (see below)
6. Push your branch and open a Pull Request

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
pip install -e ".[dev]"   # first time only — installs pytest and dev deps
pytest

# Backend tests with coverage
pytest --cov=app --cov-report=term-missing

# Frontend lint
cd frontend && npm run lint

# Frontend build check
cd frontend && npm run build
```

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

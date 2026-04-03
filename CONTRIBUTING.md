# Contributing to Flux

Thanks for your interest in contributing to Flux! This guide will help you get started.

## Getting Started

1. Fork the repository
2. Clone your fork: `git clone https://github.com/your-username/securo.git`
3. Start the stack: `docker compose up --build`
4. Open [http://localhost:3000](http://localhost:3000)

## Development Workflow

1. Create a branch from `main`: `git checkout -b feature/your-feature`
2. Make your changes
3. Run backend tests: `docker compose exec backend pytest`
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
# Backend tests
docker compose exec backend pytest

# Backend tests with coverage
docker compose exec backend pytest --cov=app --cov-report=term-missing

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

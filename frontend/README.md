# React + TypeScript + Vite

This template provides a minimal setup to get React working in Vite with HMR, and lints the
project with [Oxlint](https://oxc.rs/docs/guide/usage/linter/).

Currently, two official plugins are available:

- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react) uses [Babel](https://babeljs.io/) (or [oxc](https://oxc.rs) when used in [rolldown-vite](https://vite.dev/guide/rolldown)) for Fast Refresh
- [@vitejs/plugin-react-swc](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react-swc) uses [SWC](https://swc.rs/) for Fast Refresh

## React Compiler

The React Compiler is not enabled on this template because of its impact on dev & build performances. To add it, see [this documentation](https://react.dev/learn/react-compiler/installation).

## Linting

`npm run lint` runs Oxlint over the project; `npm run lint -- --fix` applies the fixes it knows
how to make, and `npm run lint -- --max-warnings=0` (what CI runs) fails on warnings too.

Rules live in `.oxlintrc.json`. The React Compiler rules carried over from
`eslint-plugin-react-hooks` are the ones worth knowing about here — Oxlint implements them
directly, so they need no plugin install, and in this codebase they are stricter than the
ESLint originals were (they flag `new Date()` in render, for example, and
`setState` called synchronously from an effect). Rule list and per-rule options:
<https://oxc.rs/docs/guide/usage/linter/rules.html>.

Suppressions can be written either way:

```ts
// oxlint-disable-next-line react-hooks/exhaustive-deps
// eslint-disable-next-line react-hooks/exhaustive-deps
```

## Expanding the Oxlint configuration

If you are developing a production application, we recommend enabling type-aware lint rules by
installing `oxlint-tsgolint` and editing `.oxlintrc.json`:

```sh
npm add -D oxlint-tsgolint@latest
```

```json
{
  "options": {
    "typeAware": true
  }
}
```

That turns on the `typescript/*` rules which need real type information (`no-floating-promises`,
`no-misused-promises`, …) and which the syntax-only setup above cannot check.
`options.typeAware` is only read from the root config; the equivalent CLI flag `--type-aware`
takes precedence over it. Type-aware rules are then configured like any other, e.g.
`"typescript/no-floating-promises": "error"`.

Two things to settle before switching it on here:

- The engine requires TypeScript 7.0+ and does not support legacy `tsconfig` options — this repo's
  root `tsconfig.json` still sets `baseUrl`, so it has to go first (`tsconfig.test.json` already
  documents the same removal).
- Coverage is not complete (59 of 61 typescript-eslint type-aware rules), and very large codebases
  can hit high memory use.

See <https://oxc.rs/docs/guide/usage/linter/type-aware.html>.
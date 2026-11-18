# Merging upstream into this fork

The fork formats the frontend with oxfmt and upstream does not, so a plain
merge conflicts on style in almost every file both sides touched. The `oxfmt`
merge driver removes those conflicts.

## Set up a clone

Run once per clone, from the repository root:

```bash
scripts/git/setup
```

It registers the driver for `frontend/**/*.{ts,tsx,json}` (see
`.gitattributes`) and turns on `rerere`, so Git replays a conflict resolution
it has seen before.

The driver needs oxfmt. It uses `$OXFMT`, then `frontend/node_modules/.bin/oxfmt`,
then `oxfmt` on `PATH`. Without one it falls back to the normal text merge.

## Merge

1. Run `git merge upstream/main`.
2. Resolve the conflicts that remain. These are real conflicts.
3. Run `bun run format` in `frontend/`. Files that only upstream changed or
   added never reach the driver, so they arrive unformatted.

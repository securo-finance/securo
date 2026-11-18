# Fork strings

Strings for this fork's features live here, one file per language. The files
in `../` stay byte-identical to upstream's, so upstream syncs merge them
without conflicts.

`src/lib/fork-i18n.ts` merges each file over the upstream bundle of the same
language. A language without a file here shows the fork's strings in English.

## Add a string

1. Add the key to `en.json` here, under the same feature key it would have in
   the upstream file.
2. Add it to `pt-BR.json`. `src/locales/fork-i18n.test.ts` requires it.
3. Add translations to the other files when you can. Missing ones fall back to
   English.

A key must not exist in upstream's `../en.json`. When upstream adds a key with
the same name, the test fails. Rename the fork's key.

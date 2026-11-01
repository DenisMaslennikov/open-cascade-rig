# ADR — architecture decisions

Numbering is per repository and never reused. An accepted decision is not edited: it is superseded
by a newer one, and the old file gets `Superseded by NN`.

Format: context → decision → alternatives considered → consequences (including what this decision
makes hard).

## State of the migration

`DECISIONS.md` still holds D1–D19 in their original form. They are being split into `adr/NNNN-*.md`
without changing their content, one decision per pull request; `DECISIONS.md` then becomes a pure
index. Until that is done, `DECISIONS.md` is the source of truth and this directory holds only the
decisions taken after the split.

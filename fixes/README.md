# Data fixes

Every production data fix goes through a pull request that adds two files with the same name:

| File | What goes in it |
|---|---|
| `INC-2031.txt` | the ticket, copied word for word from the ticketing system |
| `INC-2031.sql` | the script that should carry it out |

When the PR is opened, the [check-fixes](../.github/workflows/check-fixes.yml) workflow dry-runs the script
on a copy of the database, compares it with an independent reading of the ticket and posts the verdict as a
comment. The check fails unless every changed fix gets a `MATCH`.

A `MATCH` is not an approval. Someone still has to approve the PR before the script is run.

**Try it:** open a PR that adds a copy of `INC-2031` with the client ID changed in the `.sql` file only. The check
should come back `MISMATCH` and list the wrong row.

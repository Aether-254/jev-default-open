# jev-default-open

Shared hackathon source repository.

## Daily workflow

```bash
git pull --rebase
git switch -c <machine>/<feature>
# edit, test, commit
git push -u origin HEAD
```

Keep secrets and machine-local configuration out of the repository. Commit an
`.env.example` containing variable names without credentials when needed.

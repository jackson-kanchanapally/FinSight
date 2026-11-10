# Contributing to Almonds

Documentation, reproducible bug reports, accessibility improvements, tests, and code contributions are welcome. Please follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Find a useful contribution

Search the repository's Issues and pull requests before starting. For a substantial feature, explain the user need and proposed behavior in an issue first so scope and tradeoffs can be discussed. Small fixes and documentation corrections can go directly to a pull request.

Use the issue forms for bugs and feature requests. See [Support](SUPPORT.md) for the information that helps reproduce a problem and [Security](SECURITY.md) for private vulnerability reporting.

## Set up your fork

1. Fork the repository on GitHub and clone your fork.
2. Follow the [quick start](README.md#quick-start), using a private root `.env` copied from [backend/.env.example](backend/.env.example).
3. Create a branch for one change, for example `git switch -c fix/event-date`.
4. Use synthetic companies, trades, and documents when developing or recording screenshots.

The [development guide](docs/development.md) covers test commands, browser checks, migrations, and the repository layout. PostgreSQL, Ollama, Qdrant, and Telegram are not required for the default isolated backend tests.

## Keep changes reviewable

- Describe the user-visible problem and resulting behavior. Keep unrelated refactors and formatting out of the change.
- Keep financial calculations deterministic and retain dates, reporting scope, units, and source provenance. AI output must not replace validated financial facts.
- Use a new Alembic migration for schema changes. Do not rewrite a migration that may already be installed.
- Keep source adapters bounded and respect access policies. Do not bypass publisher restrictions or commit downloaded issuer documents without appropriate rights.
- Update the README, user guide, or configuration reference when behavior changes. Document missing coverage and fallback behavior.
- Add focused regression coverage for behavioral changes. Documentation-only changes usually need link and command checks rather than new tests.

Do not commit private `.env` files, tokens, credentials, portfolio records, database dumps, generated reports, or local document collections. Redact logs and screenshots before sharing them. A file being ignored by Git does not mean its contents are safe to paste into an issue.

## Check your change

Run the checks relevant to the files you changed. From the repository root:

```sh
(cd backend && .venv/bin/python -m pytest -q)
(cd frontend && npm ci && npm run build)
git diff --check
```

For a frontend behavior change, install Playwright's Chromium browser and run the relevant browser check described in the [development guide](docs/development.md). For a schema change, verify migration upgrade and rollback behavior against a disposable database. Use the existing fake providers in tests; do not make the normal test suite depend on a personal portfolio, live publisher access, or paid credentials.

In your pull request, explain what changed and why, list the checks you ran, and call out schema or configuration changes. Include redacted screenshots for visual changes. If a check could not run, state that and the reason. Link the relevant issue when one exists.

## Optional external code review

`scripts/reviewer.sh` is a developer convenience, not a contribution requirement. It requires a separately configured Pi CLI and model-provider credentials. It sends staged, unstaged, and untracked Git diffs to the configured external model provider, so inspect the change set and remove sensitive material before running it. Provider charges and terms may apply.

The default script configuration uses Velona/DeepSeek; `REVIEW_PROVIDER` and `REVIEW_MODEL` can select another configured provider and model. Keep credentials in your local environment or credential store. Results and resumable request caches are written under the ignored `.reviews/` directory. You do not need this tool or any external model account to contribute.

## License

By submitting a contribution, you agree that your contribution may be distributed under the project's existing [MIT license](LICENSE). Only submit material you have the right to contribute. Third-party market data, filings, and model weights retain their own licenses and terms.

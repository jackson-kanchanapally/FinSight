# Contributing

Thanks for considering a contribution to almonds. Issues and pull requests are welcome.

## Before opening an issue

- Search existing issues for the same problem or proposal.
- Include steps to reproduce bugs, expected and actual behavior, and relevant logs with secrets and personal data removed.
- For feature proposals, describe the user need and the behavior you expect.

## Development setup

Follow the [local setup instructions](README.md#local-setup). Create your own `.env` from `backend/.env.example`; never include credentials, private portfolio data, downloaded filings, or local database contents in a commit.

## Changes and pull requests

- Keep changes focused and explain the motivation and user-visible effects.
- Update documentation when setup, configuration, APIs, or behavior changes.
- Add or update regression coverage for behavior changes.
- Before submitting, run the relevant checks:

  ```sh
  cd backend && pytest -q
  cd ../frontend && npm ci && npm run build
  ```

- In the pull request, summarize the change, list checks run, and note any migration or configuration impact. Include screenshots for visual changes when useful.

By submitting a contribution, you agree that it may be distributed under the project's MIT license.

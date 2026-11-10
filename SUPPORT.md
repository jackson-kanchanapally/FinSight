# Getting help

Start with the [README](README.md), [user guide](docs/user-guide.md), [configuration reference](docs/configuration.md), and [self-hosting guide](docs/self-hosting.md). The API's interactive reference is available at `http://127.0.0.1:8000/docs` while Almonds is running locally.

## Bugs and questions

Search [GitHub Issues](https://github.com/jackson-kanchanapally/FinSight/issues) for an existing answer. For a reproducible bug, [open an issue using the bug report form](https://github.com/jackson-kanchanapally/FinSight/issues/new?template=bug_report.yml). A usage question can be opened as an issue too; describe the feature and the step where you got stuck. GitHub Discussions is an option only if the repository maintainers have enabled it.

Include the following when relevant:

- Your commit (`git rev-parse --short HEAD`), operating system, Python and Node versions.
- The feature you were using and the smallest steps that reproduce the problem.
- Expected and actual behavior, including the exact error message.
- Redacted logs or screenshots and whether you use fixture, live, or manual data.
- Whether PostgreSQL and any optional services for that feature are running.

Use invented symbols and transaction values when the problem can be reproduced without personal data. Do not paste `.env` files, Telegram tokens or chat identifiers, database dumps, signed report links, or private portfolio screenshots. Read [SECURITY.md](SECURITY.md) before reporting a suspected vulnerability.

## Feature requests

Use the [feature request form](https://github.com/jackson-kanchanapally/FinSight/issues/new?template=feature_request.yml) to explain the user problem, your proposed behavior, and any alternatives you considered. For a new data source, include the original publisher's documentation and access terms. Requests to bypass publisher access restrictions are outside the project's intended source-access model.

## Contributing and expectations

For a fix or documentation improvement, follow [CONTRIBUTING.md](CONTRIBUTING.md). Support is community based, with no guaranteed response or resolution time. Almonds provides research and monitoring tools; project support cannot validate an investment decision or guarantee the completeness or timeliness of external data.

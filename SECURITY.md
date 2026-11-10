# Security policy

## Reporting a vulnerability

Do not post exploit details or sensitive information in a public issue, pull request, or discussion. Open the [repository's Security tab](https://github.com/jackson-kanchanapally/FinSight/security) and use **Report a vulnerability** if private vulnerability reporting is enabled. The presence of this file does not enable that GitHub setting.

If that option is unavailable, use a private contact route published on the [repository owner's GitHub profile](https://github.com/jackson-kanchanapally). If no private route is published, [open an issue](https://github.com/jackson-kanchanapally/FinSight/issues) asking only how to send a private security report; omit the affected endpoint, exploit, and impact details until a private route is established. This project does not publish a dedicated security email address or a guaranteed response time.

A useful private report contains:

- The affected commit or version, operating system, and deployment configuration.
- A description of the impact and the access an attacker needs.
- Minimal reproduction steps using synthetic data.
- Any suggested fix or mitigation, if available.

Do not include real bot tokens, database credentials, signed report links, portfolio records, or downloaded private documents. Coordinate disclosure with the maintainers so a fix or mitigation can be assessed before exploit details are made public.

## Supported code

Security fixes target the current default branch. No separate supported release branches or security response service are declared. When reporting an issue, include the exact commit so maintainers can identify the affected code.

## Deployment boundaries

Almonds is designed for trusted local use. Several API endpoints are unauthenticated and can read or change stored research and portfolio data. Keep the API, PostgreSQL, Qdrant, and Ollama on trusted interfaces. Running the application behind an internet-accessible reverse proxy requires an independent access-control and deployment review; the development setup does not provide multi-user isolation.

Telegram credentials belong on the backend. Optional signed earnings-report links grant access to the linked report until expiry; treat those links as sensitive. Enabling public report links is separate from exposing the rest of the API.

Keep `.env`, database backups, uploaded documents, logs, and generated reports private. If a credential is exposed, revoke or rotate it with its issuing service; deleting it from the current working tree does not remove it from Git history or previously shared copies.

See the [self-hosting guide](docs/self-hosting.md) and [configuration reference](docs/configuration.md) for setup and operational details.

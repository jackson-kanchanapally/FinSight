# Almonds documentation

Start with the [project README](../README.md) for installation and a feature overview. The guides below describe the current application; implementation reports retain the evidence and limitations recorded at the time they were written.

## Use and operate Almonds

| Guide | Contents |
| --- | --- |
| [User guide](user-guide.md) | Navigation and all feature workflows, including portfolio accounting, documents, research, forecasts, and alerts |
| [Configuration](configuration.md) | Environment settings, optional services, source modes, schedulers, and notification settings |
| [Self-hosting](self-hosting.md) | Built dashboard, service operation, upgrades, backups, and troubleshooting |
| [Development](development.md) | Local servers, isolated tests, browser checks, migrations, and CI |
| [Telegram](telegram.md) | Bot connection, company alerts, receipts, retries, and publication filters |
| [NSE market data](nse-mcp.md) | Official NSE MCP: Markets page, live holdings value, stock research, symbol check, and limits |
| [Market briefs](market-briefs.md) | Pre-open and updated morning briefs, noon context, closing reviews, and scoring limits |
| [IPOs](ipos.md) | Discovery, sources, screening, weekly and pre-opening alerts |
| [Gold](gold.md) | Global and India/Hyderabad prices, news, source dates, and alert rules |
| [macOS service](production-tracking.md) | Existing LaunchAgent installer and service controls |

## Evidence and calculations

| Reference | Contents |
| --- | --- |
| [Disclosure sources](data-sources.md) | Source review, issuer matching, enabled adapters, and coverage gaps |
| [Financial extraction](financial-extraction.md) | Reporting periods, table validation, units, provenance, and calculation definitions |
| [Local AI analyst](local-ai-analyst.md) | Evidence packets, structured output, validation, and historical evaluation examples |
| [Market valuation](market-valuation.md) | Dated prices, point-in-time financial selection, TTM formulas, scenarios, and reference imports |
| [Earnings alerts](intelligent-earnings-alerts.md) | Source authenticity, freshness, earnings definitions, confidence, and signed report access |
| [Architecture](architecture/README.md) | Repository architecture and a dated inventory of interfaces and data flows |
| [Forecast accuracy report](../PHASE_8_3B_REPORT.md) | Original-versus-corrected outcomes, calibration, sample uncertainty, and audit methodology |

Source reviews describe observed access on their stated dates. They are not a guarantee of ongoing provider access. Check the application’s source status and the relevant provider’s terms before relying on coverage.

## Contribute and get help

- [Contributing](../CONTRIBUTING.md): development expectations and pull request workflow.
- [Support](../SUPPORT.md): usage questions, bug reports, and useful diagnostic information.
- [Security](../SECURITY.md): private reporting and trusted-local deployment boundaries.
- [Code of Conduct](../CODE_OF_CONDUCT.md): participation and moderation guidance.
- [License](../LICENSE) and [third-party notices](../THIRD_PARTY_NOTICES.md): project licensing and separate dependency/content terms.

## Historical implementation reports

These documents explain earlier implementations and regression results. Versioned prompts, migration milestones, source reachability, sample records, and interface descriptions may reflect their original review date. Use the user, configuration, and development guides for current setup.

| Area | Reports |
| --- | --- |
| Valuation and investor reporting | [Phase 6](phase6-report.md), [6.2](phase62-report.md), [6.3](phase63-report.md), [6.4](phase64-report.md) |
| Company Memory and quarterly reviews | [Phase 7](phase7-report.md), [7.2](phase72-report.md), [7.3](phase73-report.md), [7.4](phase74-report.md) |
| Living thesis and monitoring | [Phase 8](phase8-report.md), [8.1](phase81-report.md), [8.2](phase82-report.md), [architecture](phase8-architecture.md) |
| Expectations and autonomous research | [Phase 8.3](../PHASE_8_3_REPORT.md), [audit](../PHASE_8_3_AUDIT_REPORT.md), [autonomous research](../PHASE_8_3_AUTONOMOUS_REPORT.md), [8.3B](../PHASE_8_3B_REPORT.md) |

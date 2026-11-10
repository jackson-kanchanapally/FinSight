# Third-party software and content

Almonds' original project code is offered under the existing [MIT license](LICENSE). That license does not replace the licenses of dependencies, container images, model weights, downloaded filings, or market and news data. An installation combines components with different terms.

## PyMuPDF and MuPDF

The backend currently requires `PyMuPDF` for PDF processing. Its package metadata identifies a choice between GNU Affero GPL v3 and an Artifex commercial license. The upstream documentation confirms that PyMuPDF and MuPDF use AGPL or commercial licensing; see [PyMuPDF's license and copyright documentation](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright) and the [upstream repository](https://github.com/pymupdf/PyMuPDF).

Review the applicable upstream license and your chosen licensing route before distributing an installation or offering a hosted service. Almonds' MIT license alone does not establish permission for every use of this dependency, and this repository does not claim to include an Artifex commercial license. This notice records the dependency's terms; it does not declare a deployment compliant or relicense anyone's contribution.

## Direct dependency summary

The following is a navigation aid based on the project manifests and installed package metadata when this document was prepared. The authoritative notices are those shipped with the exact versions you install. Python dependencies currently use version ranges; the frontend lockfile records a specific resolved dependency tree. This table is not a complete transitive dependency inventory or a substitute for bundled copyright notices.

| Component | Declared upstream license | Project |
| --- | --- | --- |
| FastAPI | MIT | [fastapi/fastapi](https://github.com/fastapi/fastapi) |
| Uvicorn | BSD-3-Clause | [Uvicorn](https://uvicorn.dev/) |
| SQLAlchemy | MIT | [sqlalchemy/sqlalchemy](https://github.com/sqlalchemy/sqlalchemy) |
| Alembic | MIT | [sqlalchemy/alembic](https://github.com/sqlalchemy/alembic) |
| Pydantic | MIT | [pydantic/pydantic](https://github.com/pydantic/pydantic) |
| pydantic-settings | MIT | [pydantic/pydantic-settings](https://github.com/pydantic/pydantic-settings) |
| psycopg2-binary | LGPL with exceptions; inspect the distribution's license | [psycopg/psycopg2](https://github.com/psycopg/psycopg2) |
| HTTPX | BSD-3-Clause | [encode/httpx](https://github.com/encode/httpx) |
| APScheduler | MIT | [agronholm/apscheduler](https://github.com/agronholm/apscheduler) |
| PyMuPDF / MuPDF | AGPL v3 or Artifex commercial license | [pymupdf/PyMuPDF](https://github.com/pymupdf/PyMuPDF) |
| Beautiful Soup | MIT | [Beautiful Soup](https://www.crummy.com/software/BeautifulSoup/) |
| React and React DOM | MIT | [facebook/react](https://github.com/facebook/react) |
| Vite and its React plugin | MIT | [Vite](https://github.com/vitejs/vite), [React plugin](https://github.com/vitejs/vite-plugin-react) |
| React TypeScript definitions | MIT | [DefinitelyTyped](https://github.com/DefinitelyTyped/DefinitelyTyped) |
| TypeScript | Apache-2.0 | [microsoft/TypeScript](https://github.com/microsoft/TypeScript) |
| Playwright | Apache-2.0 | [microsoft/playwright](https://github.com/microsoft/playwright) |
| Prettier | MIT | [prettier/prettier](https://github.com/prettier/prettier) |
| pytest | MIT | [pytest-dev/pytest](https://github.com/pytest-dev/pytest) |

Inspect bundled dependencies too, including Python package extras, native libraries included in binary wheels, npm transitive packages, and Playwright's downloaded browser. Dependencies may change when the manifests or resolved versions change.

## Optional services, models, and external data

PostgreSQL, Qdrant, Ollama, and their distributed images or binaries retain their own upstream licenses. Downloaded model weights, including a selected text-generation or embedding model, have model-specific terms; changing `OLLAMA_MODEL` or `MEMORY_EMBEDDING_MODEL` can change those terms.

Company filings, issuer logos, exchange observations, RSS items, publisher excerpts, and other externally sourced content remain subject to the rights and terms of their respective owners. Public availability is not a blanket redistribution license. Source links and timestamps support provenance, but do not transfer ownership. The project's MIT license does not grant market-data redistribution or commercial-use rights.

Keep personal portfolios, local databases, downloaded document collections, and generated research outputs out of source distributions unless you have reviewed their contents and have the rights to share them.

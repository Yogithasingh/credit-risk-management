# CrediGuard AI: Application and Architecture Guide

This guide describes the project as it is currently implemented. It explains what happens when the application starts, what happens when someone uses it, where information is saved, and what each project file is for.

## 1. What the application does

CrediGuard AI is a self-hostable credit-risk decision-support demonstration. An applicant can create an account, enter a loan profile, and see a model estimate. An analyst can inspect applications and record a decision. An administrator can manage staff access, risk bands, and model versions.

The estimate is not an approval or rejection. The model provides a probability and supporting context; an authorized person records the actual decision. The model was trained on the small historical LendingClub sample included with this project, so this application is for learning and demonstration, not a validated lending system.

## 2. Architecture in one picture

```text
Applicant / Analyst / Admin browser
                 |
                 | HTML, CSS, JavaScript; same-origin HTTP(S) requests
                 v
        Caddy reverse proxy (Docker deployment)
        public entry point; HTTPS for a configured domain
                 |
                 v
        FastAPI application (one Python service)
          |          |             |
          |          |             +--> Local ML model service
          |          |                    |-- training CSV (read input)
          |          |                    +-- versioned model files
          |          |
          |          +--> SQLite database
          |                accounts, applications, scores, decisions,
          |                settings, model records, audit events
          |
          +--> PDF and CSV downloads
```

This is a **modular monolith**: the browser, API, database access, and model code are organized into separate files, but run together as one Python application. That keeps a small demo easier to install and operate. There is no separate paid model API, cloud database, JavaScript build service, or external analytics service.

When running with Docker Compose, Caddy is the only service that publishes web ports to the host. It forwards requests to FastAPI inside the private Compose network. When running directly with Uvicorn for local development, the browser talks to FastAPI without Caddy.

## 3. What happens at startup

1. Docker (or the local Python process) starts `app.main:app`.
2. FastAPI runs its startup lifespan in `app/main.py`.
3. `app/database.py` creates the data directory, model directory, SQLite tables, indexes, and default risk thresholds if needed. SQLite uses write-ahead logging (WAL).
4. The application creates the first administrator from the `BOOTSTRAP_ADMIN_*` environment settings if there is no administrator in the database yet. This is a one-time bootstrap: changing those settings later does not change an existing account's password or profile.
5. `app/security.py` loads the session-signing secret. If `SESSION_SECRET` is not supplied, the app creates a random secret file in the private data directory so sessions remain verifiable after a restart using the same stored data.
6. The model service checks for an active model artifact. If one is present and was trained from the same CSV contents, it loads it. If no suitable artifact exists, it trains a model from the bundled CSV, saves a versioned artifact and metadata, and marks the model active. First startup or retraining can take a few minutes.
7. FastAPI begins serving the browser files and API routes. `/api/health` reports whether the database and model are available.

In Docker, the Python process writes its database, model artifacts, and generated session secret under `/app/instance`. That directory is mounted to the persistent `app_data` Docker volume, so restarting or rebuilding containers does not normally erase application records.

## 4. Typical user journey

### Applicant

1. The person registers or signs in through the browser. Public applicant registration can be switched off by the operator.
2. Before entering money, the applicant selects the supported US application market and an input currency (USD, INR, GBP, or EUR), reviews a dated USD reference quote, and confirms the denomination. The browser may suggest a display locale from browser language settings; this is a number/date format preference, not a country lookup. The market choice is not identity or residence verification.
3. The browser sends form data to the FastAPI API. `app/schemas.py` checks required fields, allowed choices, country/currency codes, and value limits.
4. For a new application, FastAPI validates a short-lived currency quote tied to the applicant, stores the original numeric amounts and `country_code` / `currency_code`, converts the three monetary model features to USD, then creates a prediction. The prediction stores the quote source/date and exact converted feature values. The application starts with status `AI_ASSESSED`.
5. The applicant can see their own applications and their prediction history. The server checks ownership on each detail request; applicant accounts cannot browse another person's application. Amounts keep the application's code while the saved display locale controls number and date formatting.
6. If an analyst requests more information, the application changes to `NEEDS_MORE_INFORMATION`. The applicant can update that application without changing its market or currency, which returns it to `AI_ASSESSED` and creates another prediction record.
7. When a staff member records approval, rejection, or an information request, the applicant receives a saved in-app notification. The applicant can open it to see the current status; marking it read is saved too.

### Analyst and administrator

1. A staff member signs in and sees the staff dashboard and application queue. The API applies role checks for staff-only routes.
2. The analyst can search, filter, open an application, and start review. Starting review changes `AI_ASSESSED` to `UNDER_REVIEW`.
3. An analyst or administrator may record `APPROVED`, `REJECTED`, or `NEEDS_MORE_INFORMATION` after starting review. A note is required when requesting more information. The status change, reviewer and time, audit event, and applicant notification are committed together. A risk band never automatically makes the decision.
4. Staff can export reports and inspect model information. Administrators additionally manage user roles/active status, change risk thresholds, and retrain/activate a model.

The main workflow is:

```text
New submission -> AI_ASSESSED -> UNDER_REVIEW -> APPROVED or REJECTED
                                      |
                                      +-> NEEDS_MORE_INFORMATION
                                                |
                                  applicant resubmits details
                                                |
                                                +-> AI_ASSESSED (new prediction)
```

Staff can also rerun a prediction on an application that has not received a final `APPROVED` or `REJECTED` decision. Each rerun adds a new prediction to history instead of overwriting the old score.

## 5. How the backend handles a request

The browser code in `app/static/app.js` calls API paths such as `/api/auth/login` and `/api/applications`. The JavaScript helper sends same-origin requests with credentials and displays either the returned data or an error message.

FastAPI routes in `app/main.py` handle those requests. A typical protected request follows these steps:

1. FastAPI parses the request body and validates it with a Pydantic class from `app/schemas.py`.
2. The session dependency reads the signed session cookie and checks its signature and expiry.
3. A role helper checks whether this account is an applicant, staff member, or administrator for the requested action.
4. The route reads or writes SQLite using helpers in `app/database.py`. Database writes are committed when the connection context finishes successfully and rolled back if an error occurs.
5. For a scoring request, the route asks `ml/service.py` for a probability, applies the current risk thresholds, stores a prediction snapshot, and writes an audit event.
6. A decision request updates the application only if it is still `UNDER_REVIEW`. SQLite serializes that state check and update so reviewers cannot record contradictory outcomes. The decision, audit event, and notification share one transaction and are either all saved or all rolled back.
7. FastAPI returns JSON (or a PDF/CSV download), and the browser updates the page.

Login sets an eight-hour, HTTP-only, same-site cookie. The browser JavaScript cannot read the cookie directly. Passwords are stored as salted scrypt hashes, not as plain text. The API also validates input, applies simple in-memory request limits to login, registration, and application creation, and adds browser security headers. Those rate-limit counters live in process memory and reset when the service restarts.

## 6. How the model works

### Training data and target

The supplied file is `data/raw/accepted_2007_to_2018Q4.csv`. According to the included audit, it has 1,022 rows and 151 columns, all from December 2015. The current training pipeline removes duplicate IDs, uses individual applications, and trains only on loans with a completed result:

- `Charged Off` is the positive/default class.
- `Fully Paid` is the negative/non-default class.
- Current, late, grace-period, or other unresolved loans are excluded because their final outcome is not known.
- Joint or other application types are excluded because the application form describes one applicant.

For this file, 884 completed individual loans are used for training: 144 charged off and 740 fully paid. See [`data-audit.md`](data-audit.md) for the dataset checks and modeling limits.

### Features and training steps

The form and training pipeline use 15 fields: loan amount, term, annual income, debt-to-income ratio, prior delinquencies, FICO score, recent inquiries, open accounts, public records, revolving balance, revolving utilization, total accounts, home ownership, loan purpose, and employment length. The source data's low/high FICO values are converted to their midpoint.

The code in `ml/train.py` fills missing numeric values using training medians and missing categories using the most common training category. It scales numeric columns and converts categories into model-readable columns. It compares logistic regression, decision tree, random forest, support vector machine, and XGBoost models. It selects using validation PR-AUC, tunes the selected family with three-fold randomized search, then reports final metrics on a held-out test split. The split is stratified and uses random seed 42.

The exact winning algorithm and metrics are learned from this data during training and saved in model metadata. They are not fixed labels or fabricated scores in the interface. The evaluation only measures performance on this small, single-month historical sample; it does not establish how the model performs on a different time period or real current applicants.

### Scoring and interpretation

`ml/service.py` loads the active artifact and returns the estimated probability of default. `app/main.py` turns the probability into a 0–100 risk score and a `LOW`, `MEDIUM`, or `HIGH` band using the configured thresholds. The starting upper bounds are 0.30 for low risk and 0.60 for medium risk; administrators can change them.

Every prediction stores the model version and the thresholds used at that time. Therefore, changing today's risk bands does not rewrite the categories or threshold values saved with older predictions. The model also returns up to five one-feature-at-a-time comparisons against a typical training profile and warnings when numeric values are outside the training range. These comparisons are rough sensitivities, not causal explanations, not SHAP values, and not proof that a feature caused a result.

## 7. Where information is stored

| Information | Storage location | What it contains |
|---|---|---|
| Accounts | SQLite `users` table | Name, unique email, scrypt password hash, role, active flag, creation time, preferred country/currency and display locale |
| Applications | SQLite `applications` table | Applicant link, status and last status-change time, submitted financial/profile fields (JSON), ISO country and currency codes, assigned analyst, notes, decision, timestamps, pointer to latest prediction |
| Currency quotes | SQLite `currency_quotes` table | Short-lived quote ID tied to the requesting account, input/reference currency, rate, published date/source, and expiry |
| Prediction conversion audit | SQLite `predictions` table | The rate source/date and exact USD-normalized monetary feature values used for each assessment |
| Prediction history | SQLite `predictions` table | Probability, 0–100 score, risk band, threshold snapshot, top factors, range warnings, model version, timestamp |
| Audit trail | SQLite `audit_events` table | Actor, action, resource, details, timestamp; records logins, reviews, decisions, exports, and administrative changes |
| Applicant notifications | SQLite `notifications` table | Recipient, related application, applicant-safe message, type, creation time, and read time; each notice is linked to one unique audit event |
| Settings | SQLite `settings` table | Risk-band threshold settings |
| Model run history | SQLite `model_runs` table | Model version, algorithm, time, active flag, metrics and metadata |
| Model files | `instance/models/` locally, or `/app/instance/models/` in Docker | Versioned `.joblib` model artifact and matching `.json` metadata |
| Session signing secret | Private data directory unless configured via environment | Random persistent secret used to sign browser sessions |
| Training data | `data/raw/accepted_2007_to_2018Q4.csv` in the project image | Historical source used by the local training process; read as training input |
| Exports | Generated when downloaded | PDF or CSV response; export files are not a separate live database |

### Local versus Docker paths

- Without Docker, the default data directory is the project’s `instance/` folder. `APP_DATA_DIR` can point it elsewhere.
- With Compose, `APP_DATA_DIR` is `/app/instance`, backed by the named `app_data` volume.
- Caddy stores its HTTPS certificate/state in the named `caddy_data` volume and configuration state in `caddy_config`.
- `instance/` and `.env` are excluded from Git by `.gitignore`. The Docker build also excludes private runtime data and `.env` through `.dockerignore`.

The SQLite database file is named `credit-risk.sqlite3`. SQLite can also create temporary WAL files beside it while the database is open. Back up the persistent data volume (or the local `instance/` directory) to preserve application data and model files. Removing the Docker `app_data` volume permanently removes those records and artifacts.

The BI export labels an application's actual repayment result `NOT_OBSERVED`. The app has no repayment-results feed for its new applications, so the predicted probability must not be mistaken for an observed outcome.

## 8. What each project file does

### Application backend

| File | Responsibility |
|---|---|
| `app/main.py` | Creates the FastAPI app; startup; static page delivery; request/security middleware; login and registration; role and ownership checks; application workflow; applicant status guidance and notification routes; model/config/admin routes; dashboard; PDF/CSV exports. |
| `app/database.py` | SQLite paths and connections; schema and safe table creation for existing databases; bootstrap administrator; audit/notification helpers; risk thresholds; application/prediction queries and public response shapes. |
| `app/security.py` | Password hashing and verification, session secret creation/loading, and signing/checking the HTTP-only browser session token. |
| `app/schemas.py` | Input rules for account, application, decision, threshold, and user-management requests. |
| `app/regional.py` | Declares supported application markets, display locales, and the model's financial-unit scope. |
| `app/fx.py` | Fetches dated reference quotes and converts the three monetary model inputs to USD before scoring. |
| `app/__init__.py` | Marks `app` as a Python package. |

### Browser interface

| File | Responsibility |
|---|---|
| `app/static/index.html` | Main browser page structure: sign-in/register area and the workspace shell. |
| `app/static/styles.css` | Layout, colors, responsive behavior, forms, tables, badges, and other interface styling. |
| `app/static/app.js` | Calls the backend API, manages the signed-in browser state and navigation, builds dashboard/forms/tables/details and the notification inbox, formats amounts by saved locale, and handles user actions. Decision buttons show loading/error states and refresh the saved application after success. |
| `app/static/api-docs.html` | Page shell for the project's API reference. |
| `app/static/docs.css` | Styles the API reference page. |
| `app/static/docs.js` | Reads FastAPI's `/openapi.json` definition and displays its endpoint reference. |
| `tests/test_application_lifecycle.py` | Standard-library regression tests for review decisions, notification privacy/read state, duplicate and concurrent submissions, information requests/resubmission, and safe schema upgrades. |

### Model and data

| File | Responsibility |
|---|---|
| `ml/train.py` | Cleans and prepares the historical dataset, compares/tunes/evaluates models, calculates feature importance, and writes versioned model and metadata files. |
| `ml/service.py` | Loads or trains the active model, activates model versions, returns predictions and feature sensitivities, and checks submitted values against training ranges. |
| `ml/__init__.py` | Marks `ml` as a Python package. |
| `data/raw/accepted_2007_to_2018Q4.csv` | Bundled historical training sample supplied for this project. |
| `docs/data-audit.md` | Records the supplied dataset review, target definition, feature choices, and known limitations. |

### Setup, packaging, and deployment

| File | Responsibility |
|---|---|
| `requirements.txt` | Pins the Python packages installed by the local setup or Docker build. |
| `Dockerfile` | Builds the Python application image, installs dependencies, copies app/model/data source files, and starts Uvicorn as a non-root user. |
| `docker-compose.yml` | Runs the app and Caddy, wires their private network, maps persistent volumes, publishes Caddy web ports, and configures a health check. |
| `Caddyfile` | Tells Caddy to compress responses and forward requests to the FastAPI service. The Compose `APP_DOMAIN` selects the host/domain behavior. |
| `.env.example` | Lists environment settings to copy and customize for an installation. It is a template, not a place to store real credentials in Git. |
| `.gitignore` | Keeps local secrets, Python virtual environments, generated runtime data, and caches out of Git. |
| `.dockerignore` | Keeps local secrets, generated files, and Git internals out of the Docker build context. |
| `README.md` | Short project overview, setup and deployment commands, API overview, and links to the data audit. |
| `docs/PROJECT_GUIDE.md` | This more detailed plain-language explanation of application flow, architecture, storage, and files. |

## 9. Running locally and making it reachable remotely

For a local Docker run, copy `.env.example` to `.env`, set a unique bootstrap administrator password, keep `APP_DOMAIN=http://localhost`, then run:

```powershell
docker compose up --build -d
```

Open `http://localhost`. The first build installs the listed Python packages; first app startup trains the model if there is no matching saved model. Use `docker compose logs -f app` to see server and training progress. `docker compose down` stops the services and keeps named volumes. Do not remove the `app_data` volume unless you intend to erase saved application data.

For remote access, this repository provides the Docker/Caddy configuration but does not itself provide a public server or domain. A remote deployment needs a reachable host, a DNS name pointed to that host, inbound ports 80 and 443, a customized `.env` with an HTTPS `APP_DOMAIN`, and persistent volumes. Caddy can then obtain and renew HTTPS certificates. Do not publish the FastAPI container port directly to the public network; Compose exposes Caddy's web ports instead.

The components used here are open-source or freely available. A server, domain, backups, and internet connectivity are separate operational costs and are not supplied by the code.

## 10. Current scope and practical limits

- The implementation is a complete runnable demonstration with browser UI, API, applicant/staff roles, SQLite persistence, local model training/scoring, human review, audit trail, PDF/CSV reports, and Docker/Caddy deployment configuration.
- It has not been deployed to a public host by this project. A public URL only exists after an operator supplies and configures a host and domain.
- The model's source sample is small and covers only one issue month. Historical test metrics do not prove future performance or fairness.
- The system records application decisions but does not receive eventual repayments, so it cannot produce true default outcomes for the application's own current portfolio.
- This is not a production lending platform or an automated underwriting authority. A real deployment would need independent validation, jurisdiction-specific legal/privacy review, operational controls, monitoring, and an appropriate real-world data process.

## 11. Handy routes

- `/` — browser application.
- `/docs` — local API reference generated from FastAPI's OpenAPI definition.
- `/openapi.json` — machine-readable API definition.
- `/api/health` — database and model readiness check.
- `/api/config/regional`, `/api/currency-quotes`, and `/api/preferences/regional` — supported markets/currencies/locales, short-lived account-bound currency quotes, and saved regional display preferences.
- `/api/dashboard/summary` — applicant's own summary or staff portfolio summary.
- `/api/applications` — applicant history or staff review queue.
- `/api/notifications` and `/api/notifications/{id}/read` — the signed-in user's own notification inbox and read state.
- `/api/models/active` — staff view of active model details and recent versions.
- `/api/reports/applications.csv` and `/api/reports/powerbi.csv` — staff CSV downloads.

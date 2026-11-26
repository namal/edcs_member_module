# EDCS Microservices Configuration Guide

EDCS Member Management System

Candidate service areas and proposed deployment configuration

Document identifier: EDCS-CONFIG-001  
Version: 1.0  
Prepared: 22 November 2026  
Status: Proposed configuration for review  
Scope: Documentation only

This guide identifies the application areas that can become services and describes how a first service deployment could be configured. It extends the EDCS Microservices Feasibility Assessment with concrete naming, environments, network boundaries, database roles, messaging contracts, proxy examples and operating procedures.

All deployment files, service commands and new variables shown below are **documentation examples**. They have not been created as operational configuration, installed or deployed. Several required components do not exist in the current repository. This is a configuration blueprint for future authorized implementation, not a turnkey setup for the current application.

### Contents

1. Service areas and recommended starting point
2. Current support and implementation prerequisites
3. Target topology and service inventory
4. Proposed configuration file organization
5. Core application configuration
6. Notification service configuration
7. Reporting service configuration
8. Database ownership and access configuration
9. Messaging and reliable event delivery
10. Gateway and internal network configuration
11. Application layer Compose reference
12. Secrets, identity and authorization configuration
13. Scheduling, health and observability
14. Future deployment and validation procedure
15. Troubleshooting and rollback
16. Decisions, references and scope confirmation

## 1. Service areas and recommended starting point

### 1.1 Areas that can become services

| Application area                   | Candidate service responsibility                                                                             | Recommendation                                                                                      |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------- |
| Notifications and email            | Welcome-message delivery, attempt history, retries and provider results.                                     | First extraction candidate after durable event handoff is implemented.                              |
| Reporting and exports              | Derived dashboards, activity reports, report jobs and controlled downloads.                                  | Second candidate; preserve current access checks and show projection freshness.                     |
| Audit search                       | Searchable cross-service audit projection and collection monitoring.                                         | Later supporting service; keep original transactional audit evidence with each business owner.      |
| Interest calculation               | Calculate from versioned account snapshots and return reviewed proposals.                                    | Can become a worker; authoritative interest posting remains in the financial core.                  |
| Approval workflow                  | Hierarchies, ordered decisions, execution tracking and outcomes.                                             | Later candidate after replacing local Django request replay with explicit commands.                 |
| Membership and children            | Personal profiles, registration, contacts and identity lifecycle.                                            | Logical module first; physical extraction depends on transfer placement and posted-year safeguards. |
| Identity and organizational policy | Authentication, roles, branch/office/Lager assignments and EPF policy.                                       | Later candidate requiring a current-policy and revocation design.                                   |
| Financial core                     | Plans, openings, journals, receipts, deposits, secured lending, withdrawals, ledgers, transfers and closing. | Keep together initially to preserve shared financial transactions.                                  |

These are responsibility boundaries, not a direction to create eight services immediately. The first practical target retains one authoritative financial application and adds notification delivery and reporting as supporting capabilities.

### 1.2 Why financial functions remain together

Receipt credits, withdrawal release, transfer placement/page changes and system year-end closing currently share PostgreSQL transactions and locks. Splitting their databases would change atomicity and require a new consistency protocol. A configuration file cannot solve that business redesign by itself.

The initial architecture therefore separates independent delivery/read workloads while retaining authoritative account writes. It can later evolve further when a specific capability has a clear owner, stable contract and approved failure semantics.

### 1.3 What the initial setup would contain

There would be three business components: the core staff application, notification delivery and reporting. Reporting can have separate API and projection-worker processes while still remaining one capability and data owner. The outbox relay and scheduled interest process belong to the core, rather than becoming independent financial authorities.

A gateway, PostgreSQL stores, broker, secret distribution and monitoring support the business components. These infrastructure responsibilities require configuration and ownership even though they are not business microservices.

## 2. Current support and implementation prerequisites

### 2.1 Existing versus proposed features

| Item                                  | Current application                                                         | Required before the proposed setup works                                              |
| ------------------------------------- | --------------------------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| Core Django application               | Present, including `config.wsgi:application` and `config.asgi:application`. | Package an approved production server/runtime image.                                  |
| Core PostgreSQL environment variables | Present: DB_NAME, DB_USER, DB_PASSWORD, DB_HOST and DB_PORT.                | Supply least-privilege runtime credentials and deployment networking.                 |
| Secret and email settings             | Present: DJANGO_SECRET_KEY and EMAIL_* configuration.                       | Supply secrets privately; keep only the delivery owner responsible for SMTP.          |
| Notification service                  | Not present as an independent service.                                      | Implement durable requests, delivery/inbox state and a worker entry point.            |
| Reporting service                     | Not present as an independent API/projector.                                | Implement a derived store, protected API, authorization contract and event consumer.  |
| Core outbox                           | Not present as the proposed durable integration contract.                   | Add transactional outbox records and a reliable relay.                                |
| Broker producer/consumer integration  | Not configured by the current requirements.                                 | Select and pin a client; implement confirms, acknowledgments and redelivery handling. |
| New internal policy/report APIs       | Not present as the proposed service contract.                               | Implement delegated authorization, versioning and current scope checks.               |
| Service secret-file loaders           | Not implemented for the proposed *_FILE variables.                          | Add deployment adapters that read files and supply supported application settings.    |
| Service readiness/liveness commands   | Not present under the proposed `/app/bin/` contract.                        | Implement nonmutating probes in each deployment image.                                |
| Container images and deployment files | Not supplied by this guide as operational files.                            | Build, review and pin images; create approved configuration in a later task.          |

### 2.2 Important variable distinction

Existing variables are consumed by the current settings file. Proposed variables such as BROKER_URL_FILE, REPORTING_BASE_URL, POLICY_BASE_URL, OUTBOX_BATCH_SIZE and SERVICE_SIGNING_KEY_FILE have **no effect on today's application merely because they appear in an environment file**. They require the future code/adapters described in this guide.

Likewise, the current Django settings do not read DJANGO_SECRET_KEY_FILE or DB_PASSWORD_FILE automatically. Mounting a secret file is not sufficient until a wrapper or settings loader reads it. The proposed container entry-point contract explicitly includes that responsibility.

### 2.3 Configuration notation

`<...>` denotes an operator-supplied value or an agreed implementation choice. `${NAME:?message}` in a Compose example is a required interpolation value, not an application secret lookup. Hostnames under `.example` and service names such as `core-db` are proposed names rather than discovered infrastructure.

No credential, token, actual database password or private signing key is included. Image versions should be approved and pinned by digest during implementation rather than copied from a floating latest tag.

## 3. Target topology and service inventory

### 3.1 Logical request and event flow

The browser would access one HTTPS gateway and retain the Django workspace session. The gateway would proxy the user interface to core-web. Core would execute authoritative business commands and request protected reports over a private service interface.

The core transaction would write business data, audit evidence and an outbox intent together. A relay would publish committed events. Notification workers and report projectors would consume their allowed events using their own identities and databases. Reporting would return derived results only after current requester scope is checked.

This preserves the existing user-facing origin and avoids assuming that the browser can safely call a new reporting service with a Django session cookie.

### 3.2 Proposed service names and ports

| Service or endpoint       | Proposed internal endpoint                 | Exposure and ownership                                                                                 |
| ------------------------- | ------------------------------------------ | ------------------------------------------------------------------------------------------------------ |
| gateway                   | 80 and 443                                 | Only public application listener; HTTP redirects to HTTPS.                                             |
| core-web                  | `core-web:8000`                            | Private HTTP listener behind gateway; authoritative application owner.                                 |
| outbox-relay              | No HTTP listener required.                 | Core-owned background process; reads core outbox and publishes events.                                 |
| notification-worker       | No public/API listener required initially. | Delivery worker with notification database, broker and SMTP access.                                    |
| reporting-api             | `reporting-api:8000`                       | Private API called by core with validated service/user context.                                        |
| reporting-projector       | No public listener required.               | Reporting-owned consumer that updates derived records.                                                 |
| core-db                   | `core-db:5432`                             | Authoritative core database endpoint; private and not accessible to reporting/notification identities. |
| notification-db           | `notification-db:5432`                     | Delivery-state database endpoint.                                                                      |
| reporting-db              | `reporting-db:5432`                        | Projection and report-job database endpoint.                                                           |
| broker                    | `broker:5671` for TLS                      | Private broker endpoint with separate publishers/consumers.                                            |
| scheduled interest runner | No public listener.                        | Core-owned scheduled command; no second scheduler for the same operation.                              |

These DNS names must be provisioned as actual network aliases, service records or managed endpoints. They are not created by listing them in this document. A development-only broker may use 5672 without TLS on an isolated network; that is a separate lab configuration, not the production baseline.

### 3.3 Database deployment choices

The three databases can be separate logical databases on an appropriately controlled PostgreSQL cluster or separate server instances. A shared cluster reduces infrastructure count but retains a shared failure and administration boundary. Separate logical databases alone do not establish host-level isolation or independent recovery.

Choose the physical layout after workload, recovery and operational ownership are agreed. The first design must still use separate service roles and prevent cross-owner writes.

## 4. Proposed configuration file organization

### 4.1 Future deployment directory

The following tree is a naming proposal. No corresponding deployment files have been created by this task.

```text
deployment/
  compose.yaml
  platform.env
  gateway/
    default.conf
  env/
    core.env
    relay.env
    notifications.env
    reporting-api.env
    reporting-projector.env
  secrets/
    core-django-secret
    core-db-password
    notification-db-password
    reporting-api-db-password
    reporting-projector-db-password
    core-relay-db-password
    relay-broker-url
    notification-broker-url
    reporting-broker-url
    gmail-app-password
    core-reporting-client-credential
    reporting-policy-client-credential
    database-ca.pem
    broker-ca.pem
    tls-certificate.pem
    tls-private-key.pem
```

Database/broker server bootstrap, user provisioning, TLS trust bundles, backup configuration and monitoring belong to the approved infrastructure deployment. The application-layer Compose reference later in this guide assumes those stores and network aliases already exist.

### 4.2 File responsibilities

platform.env would supply image references and host-level deployment interpolation. The env files would contain service-specific nonsecret settings and secret-file locations. Secret values would be distributed separately using protected files or an approved secret manager. Do not copy the entire current root `.env` into every image or container.

The current project can retain its local `.env` during ordinary development. Future deployment images should exclude local environment/credential files and receive deliberate runtime configuration. A `.gitignore` rule is not by itself an image-build exclusion rule.

### 4.3 Environment separation

Development, staging and production need separate Django secrets, database credentials, broker users/vhosts, service credentials and SMTP policy. Do not point a test service at production just because the schema looks compatible. Prefer a test email sink in nonproduction rather than sending welcome messages for copied member/user records.

Keep a reviewed nonsecret configuration template and a deployment inventory identifying who supplies each secret. Operators should be able to validate configuration without printing resolved secrets into logs.

## 5. Core application configuration

### 5.1 Existing settings example

This example uses settings already recognized by the current application. Placeholder secrets are for documentation only. The hostnames are proposed deployment endpoints.

```dotenv
DJANGO_SECRET_KEY=<load-from-core-secret>
DJANGO_DEBUG=false
DJANGO_ALLOWED_HOSTS=edcs.example

DB_NAME=edcs_core
DB_USER=edcs_core_runtime
DB_PASSWORD=<load-from-core-db-secret>
DB_HOST=core-db
DB_PORT=5432

SESSION_IDLE_TIMEOUT=900
FINANCIAL_YEAR_START_MONTH=1
```

Retain the agreed financial-year start; changing it is a business/configuration migration decision. The session timeout is an example matching the current default. The actual public hostname must be selected before release.

### 5.2 Proposed additional core settings

The following names would be new application/deployment settings. They must be implemented before they can affect behavior.

```dotenv
SERVICE_NAME=core
REPORTING_BASE_URL=http://reporting-api:8000
REPORTING_REQUEST_TIMEOUT_SECONDS=5
POLICY_VERSION_CHECK_REQUIRED=true
NOTIFICATION_DELIVERY_MODE=outbox
SERVICE_CLIENT_CREDENTIAL_FILE=/run/secrets/core-reporting-client-credential
OUTBOX_ENABLED=true
OUTBOX_SCHEMA_VERSION=1
```

The internal HTTP address is an example for the isolated Compose model. A production design requiring encryption between service processes must use trusted TLS endpoints and server certificate verification, or an equivalent approved mutually authenticated transport. Private DNS does not itself encrypt requests.

### 5.3 Required production Django changes to design later

The current environment loader does not expose all transport/proxy/database TLS controls needed by this blueprint. A later implementation would need to define SECURE_SSL_REDIRECT, trusted SECURE_PROXY_SSL_HEADER handling, HSTS rollout, CSRF trusted origins where needed, database TLS options and any service-client settings.

SECURE_PROXY_SSL_HEADER should be trusted only when the gateway overwrites the relevant header and the application is not directly reachable by untrusted clients. Turning on a setting with an untrusted forwarded header can misclassify insecure requests. Do not assume that setting an unrecognized environment variable already changes Django's settings.

### 5.4 Core image entry-point contract

The proposed core image would provide `/app/bin/start` with modes `web` and `outbox-relay`, and `/app/bin/healthcheck` with the appropriate mode. These files do not exist in the current repository. The image contract would need to:

1. Read authorized secret files and populate the corresponding supported settings without logging values.
2. Run web mode using a pinned production WSGI/ASGI server against the existing application entry point.
3. Run relay mode using the future durable outbox publisher.
4. Execute health probes without posting money, sending messages or applying migrations.
5. Shut down gracefully and preserve durable work state.

Database migrations should be a controlled one-off deployment action under a migration role, not an automatic command run concurrently by every web/relay replica.

## 6. Notification service configuration

### 6.1 Responsibility and storage

The notification service would store delivery request identity, approved recipient/template data, attempt count, next attempt, provider result and final state. It would consume committed notification requests and have no permission to alter member balances or the core database.

The current `notify_user_created` function performs SMTP work in an on-commit callback. That is not the proposed durable service. Extraction requires changing the handoff, defining event data and providing consumer/delivery persistence in later authorized work.

### 6.2 Proposed worker settings

```dotenv
SERVICE_NAME=notifications
DB_NAME=edcs_notifications
DB_USER=edcs_notification_runtime
DB_HOST=notification-db
DB_PORT=5432
DB_PASSWORD_FILE=/run/secrets/notification-db-password

BROKER_URL_FILE=/run/secrets/notification-broker-url
BROKER_VHOST=/edcs-prod
NOTIFICATION_QUEUE=edcs.notifications.v1
CONSUMER_PREFETCH=10
MAX_DELIVERY_ATTEMPTS=5
RETRY_DELAYS_SECONDS=60,300,900,3600
DELIVERY_STATE_RETENTION_DAYS=<agree-retention>
```

These are proposed worker configuration names. Initial numeric retry/prefetch values are planning examples and need load/provider-policy validation. Five total attempts would mean one initial attempt plus up to four delayed retries using the four listed delays.

Do not keep notification delivery inside an unbounded database transaction while waiting on SMTP. Use durable claim/attempt state, a bounded network timeout and a defined recovery path for an interrupted in-flight attempt.

### 6.3 Gmail SMTP example

The requested sender can be configured in the future notification deployment as follows. This example does not modify the current `.env`.

```dotenv
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_HOST_USER=namal.spencer@gmail.com
EMAIL_HOST_PASSWORD_FILE=/run/secrets/gmail-app-password
EMAIL_USE_TLS=true
EMAIL_USE_SSL=false
EMAIL_TIMEOUT=10
DEFAULT_FROM_EMAIL=EDCS <namal.spencer@gmail.com>
PUBLIC_APPLICATION_URL=https://edcs.example
```

EMAIL_HOST_PASSWORD_FILE would require the new service's loader; the current application expects EMAIL_HOST_PASSWORD. A deployment adapter could read the file and set that supported variable for a Django-based worker. The Gmail app password belongs only to the component performing delivery.

Google's documentation describes Gmail SMTP with TLS/STARTTLS on port 587 and app passwords for eligible accounts with 2-Step Verification. Provider eligibility and sending limits should be checked for the intended account before production rollout. [Gmail SMTP settings](https://support.google.com/mail/answer/7104828?hl=en), [Google app passwords](https://support.google.com/accounts/answer/185833).

### 6.4 Delivery-state rules

Use a stable notification ID to deduplicate repeated broker delivery. Distinguish queued, claimed, attempted, provider-accepted and failed outcomes. Do not report delivered merely because an event was queued or the SMTP server accepted it.

An SMTP acknowledgment can be lost after provider acceptance, so exactly-once external email delivery is not guaranteed by an inbox table alone. Agree how uncertain attempts and possible duplicate messages will be handled. Retry only transient failures; invalid recipient/authorization/template failures need classification rather than endless requeueing.

## 7. Reporting service configuration

### 7.1 API and projector responsibilities

reporting-projector would consume allowed committed events and maintain a derived reporting store. reporting-api would serve authorized queries and report jobs from that store. Both belong to the reporting capability and can use one reporting database with separately restricted roles if operationally useful.

Neither process should connect to the core database as a general-purpose writer. Financial eligibility, account posting and collateral decisions must continue to use authoritative core services.

### 7.2 Proposed reporting API settings

```dotenv
SERVICE_NAME=reporting-api
HTTP_BIND=0.0.0.0:8000
DB_NAME=edcs_reporting
DB_USER=edcs_reporting_api
DB_HOST=reporting-db
DB_PORT=5432
DB_PASSWORD_FILE=/run/secrets/reporting-api-db-password

POLICY_BASE_URL=http://core-web:8000/internal/v1/access
POLICY_TIMEOUT_SECONDS=3
POLICY_FAILURE_MODE=deny
EXPECTED_SERVICE_AUDIENCE=edcs-reporting
SERVICE_CLIENT_CREDENTIAL_FILE=/run/secrets/reporting-policy-client-credential
REPORT_CACHE_ENABLED=false
EXPORT_LINK_TTL_SECONDS=300
```

The internal policy endpoint, service audience check, export links and all names above are proposed. They are not existing EDCS interfaces. A real implementation should use its approved delegated-identity mechanism and scoped service credentials, not trust a user ID header supplied by an external browser.

Use different client credentials for different callers and audiences; the reference uses distinct core-to-reporting and reporting-to-policy secret files.

### 7.3 Proposed projector settings

```dotenv
SERVICE_NAME=reporting-projector
DB_NAME=edcs_reporting
DB_USER=edcs_reporting_projector
DB_HOST=reporting-db
DB_PORT=5432
DB_PASSWORD_FILE=/run/secrets/reporting-projector-db-password

BROKER_URL_FILE=/run/secrets/reporting-broker-url
BROKER_VHOST=/edcs-prod
REPORTING_QUEUE=edcs.reporting.v1
CONSUMER_PREFETCH=50
PROJECTION_SCHEMA_VERSION=1
LAG_WARNING_SECONDS=60
LAG_CRITICAL_SECONDS=300
```

The API and projector receive different roles and password mounts. The API should have the minimum access for read queries and report-job state; the projector should write only its owned projection/inbox structures. The sample Compose below uses the corresponding separate secret mounts.

Lag thresholds are example alert thresholds, not measured service-level commitments. A report must show its source watermark. Passing a lag threshold does not make a stale balance safe for an authoritative financial decision.

### 7.4 Authorization and report correctness

Current branch, office, Lager, EPF and module grants must be evaluated before exposing reports or exports. Historical source-page access has separate retained rules. A stale report copy of a member's location is not an authorization grant.

Start with report caching disabled until the key includes the correct policy/resource scope and revocation behavior. Preserve opening effective-date rules, child/parent account separation, correction links and distinction between journal movements versus status/snapshot events. A withdrawal release event and its linked journal debit must not become two debits in totals.

## 8. Database ownership and access configuration

### 8.1 Proposed database and role matrix

| Database           | Runtime identities                                      | Permitted data                                                                                            |
| ------------------ | ------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| edcs_core          | edcs_core_runtime; optional narrower edcs_outbox_relay. | Authoritative business records, local audit and outbox; relay restricted to claimed outbox/publish state. |
| edcs_notifications | edcs_notification_runtime.                              | Inbox/delivery/attempt state and minimum approved recipient data.                                         |
| edcs_reporting     | edcs_reporting_api and edcs_reporting_projector.        | Derived reporting tables, projector inbox/watermarks and controlled report-job state.                     |

Each database should have a separate owner/migration identity. Runtime users should not have SUPERUSER, CREATEDB, CREATEROLE or REPLICATION. Grant only required schema/table/sequence privileges and define default privileges under the actual role that creates future objects.

There is no need for application services to own the PostgreSQL cluster. Database provisioning, backups and administrative credentials belong to a controlled operations boundary. A source-side outbox relay may remain under the core owner while still receiving narrower SQL permissions than the web runtime.

### 8.2 Illustrative privilege pattern

The following SQL is an unexecuted pattern for a later database-administrator review. It omits passwords and the exact per-table grants because those depend on the approved schema and runtime responsibilities.

```sql
CREATE ROLE edcs_reporting_owner NOLOGIN;
CREATE ROLE edcs_reporting_api LOGIN
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
CREATE ROLE edcs_reporting_projector LOGIN
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;

CREATE DATABASE edcs_reporting OWNER edcs_reporting_owner;
REVOKE ALL ON DATABASE edcs_reporting FROM PUBLIC;
GRANT CONNECT ON DATABASE edcs_reporting
  TO edcs_reporting_api, edcs_reporting_projector;
```

Before these roles are usable, an operator would provision authentication through an approved mechanism, connect to the intended database, grant explicit schema/table/sequence rights and configure migration/default privileges. Do not copy a broad ALL PRIVILEGES grant across every service or run this example against the current database without a migration plan.

### 8.3 TLS and connection settings

Remote production database connections should use the organization's approved encryption and certificate-verification configuration. For psycopg, TLS options belong in the connection configuration; the current DB_* environment variables alone do not implement an arbitrary sslmode setting.

A future adapter should map reviewed variables such as DB_SSLMODE and DB_SSLROOTCERT into supported Django database OPTIONS. Verify server certificate/hostname trust rather than assuming an encrypted connection necessarily validates the correct server. Isolated lab networking and production TLS are different configurations.

The Compose reference sets the proposed DB_SSLMODE to verify-full and mounts an approved database CA. Infrastructure must supply a certificate valid for each configured database hostname and clients must implement the mapping into their database driver. A plaintext-only local PostgreSQL container would not satisfy that reference; a separate explicit lab override would be required.

### 8.4 Migrations and restores

Run each service's migrations once per release under its migration identity. Runtime replicas should not race to create/alter the same schema. Restore the authoritative database before rebuilding dependent projections and distinguish recovery of original money records from replay of report events.

Keep member/account/journal references stable. Do not split the financial database in the initial configuration, because receipt, transfer, withdrawal and closing invariants still depend on one transaction owner.

## 9. Messaging and reliable event delivery

### 9.1 Proposed broker layout

RabbitMQ is used as a reference broker in this guide, not as an installed or mandatory product. An alternative can be selected if it meets the same durable delivery, recovery and access requirements. Pin the approved broker/client versions during implementation.

| Resource                | Proposed name                | Behavior                                                                     |
| ----------------------- | ---------------------------- | ---------------------------------------------------------------------------- |
| Application vhost       | `/edcs-prod`                 | Isolated from development/staging and limited to explicit identities.        |
| Business event exchange | `edcs.events.v1`             | Durable topic exchange for committed business facts.                         |
| Notification queue      | `edcs.notifications.v1`      | Durable queue bound only to approved notification routing keys.              |
| Reporting queue         | `edcs.reporting.v1`          | Durable queue bound only to events required for projections.                 |
| Retry exchange          | `edcs.retry.v1`              | Delay/retry routing implemented using reviewed queue policies or scheduling. |
| Dead-letter exchange    | `edcs.dead.v1`               | Routes messages that exceed bounded recovery policy.                         |
| Notification dead queue | `edcs.notifications.dead.v1` | Access-controlled inspection/recovery.                                       |
| Reporting dead queue    | `edcs.reporting.dead.v1`     | Projection recovery independent of live notifications.                       |

Queue durability alone is not a full recovery design. Decide whether replicated/quorum queues and multiple broker nodes are required by availability targets. A single broker is a single failure boundary even when all queue names are durable.

### 9.2 Publisher and consumer identities

The relay publisher should publish only to its allowed exchange. The notification consumer should read only its own queues and have only the additional write permissions necessary for its approved retry/outcome flow. The reporting projector should not consume SMTP credential-bearing messages or alter core records.

Provision topology through a controlled operator identity or a narrowly authorized setup process. Runtime users should not automatically configure arbitrary exchanges/queues. Do not use a shared bootstrap administrator as every service's broker identity.

### 9.3 Proposed relay configuration

```dotenv
SERVICE_NAME=outbox-relay
DB_NAME=edcs_core
DB_USER=edcs_outbox_relay
DB_HOST=core-db
DB_PORT=5432
DB_PASSWORD_FILE=/run/secrets/core-relay-db-password

BROKER_URL_FILE=/run/secrets/relay-broker-url
EVENT_EXCHANGE=edcs.events.v1
OUTBOX_BATCH_SIZE=100
OUTBOX_POLL_INTERVAL_SECONDS=2
PUBLISH_CONFIRM_TIMEOUT_SECONDS=10
PUBLISH_RETRY_BACKOFF_SECONDS=5,30,120,600
```

The broker URL secret would contain the appropriate service credentials, TLS endpoint and correctly encoded vhost. Avoid exposing it through debug logs or command-line process arguments. The relay module/entry point and its configuration parser are proposed implementation requirements.

Broker clients must verify the server certificate and hostname using the approved trust bundle. The reference mounts a broker CA and sets proposed BROKER_TLS_VERIFY and BROKER_CA_FILE values. Those values require an implemented client configuration adapter, and the broker certificate must match the hostname in its service URL.

### 9.4 Required delivery logic

Commit the business write and outbox row in the same owning transaction. Relay only committed rows, await publisher confirms, and record publication state durably. If the relay crashes after publish but before recording confirmation, a repeated publication must be safe.

Consumers should use manual acknowledgments after their own durable processing commits. Record event IDs in an inbox/unique constraint so redelivery does not apply the projection or business effect again. Preserve aggregate version/ordering and classify permanent validation failures separately from temporary dependency failures. RabbitMQ documents publisher confirms and consumer acknowledgments as distinct responsibilities. [RabbitMQ acknowledgments and confirms](https://www.rabbitmq.com/docs/confirms).

### 9.5 Event meaning and retry boundaries

Financial messages should describe facts such as ContributionPosted, JournalEntryPosted or InterestYearPosted after the authoritative transaction. A reporting consumer must not interpret those facts as commands to credit the account again.

No broker configuration provides exactly-once financial effects automatically. The owner still needs operation identity, unique constraints and transactional validation. SMTP delivery uncertainty requires separate handling from financial operation idempotency. Dead-letter replay should never bypass current authorization or resend historical notifications accidentally.

## 10. Gateway and internal network configuration

### 10.1 Public route ownership

Keep the existing public HTML/JSON routes directed to core-web initially, including login, members, contributions, deposits, approvals, ledgers and closing. These routes already contain important session, form and authorization behavior. A URL prefix alone is not a safe reason to bypass those checks by routing straight to another service.

The reporting service stays private. A future core backend route can request a report on behalf of the signed-in user and return the authorized result/job status. Internal policy, service metadata and operational health endpoints should not be exposed accidentally through the public catch-all proxy.

### 10.2 Illustrative Nginx configuration

The following is a proposed file at `deployment/gateway/default.conf`, included by an approved Nginx image in its HTTP configuration context. It assumes the certificate/private key and static directory are provisioned. The placeholder hostname must be replaced before use.

```nginx
upstream edcs_core {
    server core-web:8000;
    keepalive 16;
}

server {
    listen 80;
    server_name edcs.example;
    return 308 https://edcs.example$request_uri;
}

server {
    listen 443 ssl;
    server_name edcs.example;

    ssl_certificate /run/secrets/tls-certificate;
    ssl_certificate_key /run/secrets/tls-private-key;
    ssl_protocols TLSv1.2 TLSv1.3;
    client_max_body_size 2m;

    location /static/ {
        alias /srv/edcs/static/;
    }

    location /internal/ {
        return 404;
    }

    location /health/ {
        return 404;
    }

    location / {
        proxy_pass http://edcs_core;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Request-ID $request_id;
        proxy_cache off;
        proxy_connect_timeout 5s;
        proxy_read_timeout 30s;
    }
}
```

This example assumes gateway is the first trusted HTTP hop. A load balancer in front of it changes the client-IP/protocol trust design and must be accounted for deliberately. The upstream private HTTP connection is an illustrative single-host integration choice; multi-host production requirements may require authenticated backend TLS.

For this static upstream example, an operator must reload the gateway after backend address replacement, or implement reviewed dynamic service discovery. A stable service name does not guarantee that a long-running proxy automatically refreshes a previously resolved container IP.

The proxy_pass without a URI suffix preserves the original application path. The public proxy explicitly overwrites forwarded protocol/client metadata instead of trusting arbitrary client-provided values. [Nginx proxy module](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_pass).

### 10.3 Certificates, static assets and HSTS

Use a certificate for the actual public hostname and an approved renewal/reload process. Host placeholders and self-signed local certificates do not constitute a validated production TLS deployment. A self-signed staging certificate requires an explicitly trusted staging CA, not disabled certificate verification.

Populate the static artifact directory from the core release using the existing static collection/build process in a controlled build or one-off deployment step. Serving only the Python web listener is not a complete production static configuration. The reference Compose volume assumes those artifacts have been populated; it does not create them automatically.

HSTS is deliberately not turned on blindly in this sample. After validating all relevant HTTPS paths/hostnames, select its max-age and any subdomain policy and apply it at the agreed authoritative layer. The application also needs the trusted-proxy settings described earlier to avoid insecure URL generation or redirect loops.

### 10.4 Proposed network segmentation

| Network                | Members and purpose                                      | Restriction                                                                                 |
| ---------------------- | -------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| edge                   | Gateway's public-facing network.                         | Publish only approved HTTP/HTTPS listeners.                                                 |
| service                | Gateway, core-web and reporting-api.                     | Private interservice requests; no database or broker management interface exposed publicly. |
| edcs_core_data         | Core web/relay and core database endpoint.               | Reporting/notification processes do not join.                                               |
| edcs_notification_data | Notification worker and its database endpoint.           | No core financial database access.                                                          |
| edcs_reporting_data    | Reporting API/projector and reporting database endpoint. | No authoritative financial write access.                                                    |
| edcs_events            | Relay, approved consumers and broker endpoint.           | Separate broker identities and routing permissions.                                         |
| smtp_egress            | Notification worker outbound network.                    | Restrict through host/firewall policy to approved destinations/ports as needed.             |

Docker networking supplies reachability and service discovery, not complete identity or outbound-domain filtering. A network named smtp_egress does not itself restrict traffic to Gmail. Host/network policy must enforce that if required. Internal Docker names should be used for container-to-container addresses; localhost inside one container means that same container. [Compose networking](https://docs.docker.com/compose/how-tos/networking/).

## 11. Application layer Compose reference

### 11.1 Assumptions and missing prerequisites

This is an application-layer reference, not a complete infrastructure installer. It assumes pre-provisioned PostgreSQL/broker endpoints attached to external networks with aliases core-db, notification-db, reporting-db and broker. Operators must create those networks, configure DNS/aliases, database roles, broker topology, TLS and backups separately. They do not exist merely because this example mentions them.

It also assumes approved images providing the `/app/bin/start` and `/app/bin/healthcheck` contracts, future core outbox/policy/report integrations, the named nonsecret env files and the secret files. None of those future artifacts was created for this task. The existing application alone cannot run this Compose file successfully.

Use only nonsecret portions of the earlier environment examples in env files. Supply secret-file variables through the explicit environment entries below. A future loader should reject supplying both a direct secret value and its *_FILE alternative, rather than accidentally choosing a placeholder. Keep the original root `.env` out of deployment image contents.

### 11.2 Proposed image interpolation values

The following is the future platform.env format. These are image references, not credentials.

```dotenv
GATEWAY_IMAGE=<approved-nginx-image-with-digest>
CORE_IMAGE=<built-core-image-with-digest>
NOTIFICATION_IMAGE=<built-notification-image-with-digest>
REPORTING_IMAGE=<built-reporting-image-with-digest>
```

An approved image reference should identify the intended registry/repository and immutable release/digest. No latest tag, unverified version or new dependency installation is selected by this guide.

### 11.3 Proposed Compose structure

```yaml
name: edcs

services:
  gateway:
    image: ${GATEWAY_IMAGE:?Set approved gateway image}
    ports:
      - '80:80'
      - '443:443'
    volumes:
      - ./gateway/default.conf:/etc/nginx/conf.d/default.conf:ro
      - edcs_static:/srv/edcs/static:ro
    secrets:
      - tls-certificate
      - tls-private-key
    networks: [edge, service]
    depends_on:
      core-web:
        condition: service_healthy
    restart: unless-stopped

  core-web:
    image: ${CORE_IMAGE:?Set built core image}
    command: ['/app/bin/start', 'web']
    env_file: [./env/core.env]
    environment:
      DJANGO_SECRET_KEY_FILE: /run/secrets/core-django-secret
      DB_PASSWORD_FILE: /run/secrets/core-db-password
      SERVICE_CLIENT_CREDENTIAL_FILE: /run/secrets/core-reporting-client-credential
      DB_SSLMODE: verify-full
      DB_SSLROOTCERT: /run/secrets/database-ca
    secrets:
      - core-django-secret
      - core-db-password
      - core-reporting-client-credential
      - database-ca
    networks: [service, core-data]
    healthcheck:
      test: ['CMD', '/app/bin/healthcheck', 'web']
      interval: 15s
      timeout: 5s
      retries: 3
      start_period: 30s
    restart: unless-stopped

  outbox-relay:
    image: ${CORE_IMAGE:?Set built core image}
    command: ['/app/bin/start', 'outbox-relay']
    env_file: [./env/relay.env]
    environment:
      DJANGO_SECRET_KEY_FILE: /run/secrets/core-django-secret
      DB_PASSWORD_FILE: /run/secrets/core-relay-db-password
      BROKER_URL_FILE: /run/secrets/relay-broker-url
      DB_SSLMODE: verify-full
      DB_SSLROOTCERT: /run/secrets/database-ca
      BROKER_TLS_VERIFY: 'true'
      BROKER_CA_FILE: /run/secrets/broker-ca
    secrets:
      - core-django-secret
      - core-relay-db-password
      - relay-broker-url
      - database-ca
      - broker-ca
    networks: [core-data, events]
    restart: unless-stopped

  notification-worker:
    image: ${NOTIFICATION_IMAGE:?Set built notification image}
    command: ['/app/bin/start', 'consumer']
    env_file: [./env/notifications.env]
    environment:
      DB_PASSWORD_FILE: /run/secrets/notification-db-password
      BROKER_URL_FILE: /run/secrets/notification-broker-url
      EMAIL_HOST_PASSWORD_FILE: /run/secrets/gmail-app-password
      DB_SSLMODE: verify-full
      DB_SSLROOTCERT: /run/secrets/database-ca
      BROKER_TLS_VERIFY: 'true'
      BROKER_CA_FILE: /run/secrets/broker-ca
    secrets:
      - notification-db-password
      - notification-broker-url
      - gmail-app-password
      - database-ca
      - broker-ca
    networks: [notification-data, events, smtp-egress]
    restart: unless-stopped

  reporting-api:
    image: ${REPORTING_IMAGE:?Set built reporting image}
    command: ['/app/bin/start', 'api']
    env_file: [./env/reporting-api.env]
    environment:
      DB_PASSWORD_FILE: /run/secrets/reporting-api-db-password
      SERVICE_CLIENT_CREDENTIAL_FILE: /run/secrets/reporting-policy-client-credential
      DB_SSLMODE: verify-full
      DB_SSLROOTCERT: /run/secrets/database-ca
    secrets:
      - reporting-api-db-password
      - reporting-policy-client-credential
      - database-ca
    networks: [service, reporting-data]
    healthcheck:
      test: ['CMD', '/app/bin/healthcheck', 'api']
      interval: 15s
      timeout: 5s
      retries: 3
      start_period: 30s
    restart: unless-stopped

  reporting-projector:
    image: ${REPORTING_IMAGE:?Set built reporting image}
    command: ['/app/bin/start', 'projector']
    env_file: [./env/reporting-projector.env]
    environment:
      DB_PASSWORD_FILE: /run/secrets/reporting-projector-db-password
      BROKER_URL_FILE: /run/secrets/reporting-broker-url
      DB_SSLMODE: verify-full
      DB_SSLROOTCERT: /run/secrets/database-ca
      BROKER_TLS_VERIFY: 'true'
      BROKER_CA_FILE: /run/secrets/broker-ca
    secrets:
      - reporting-projector-db-password
      - reporting-broker-url
      - database-ca
      - broker-ca
    networks: [reporting-data, events]
    restart: unless-stopped

networks:
  edge: {}
  service:
    internal: true
  core-data:
    external: true
    name: edcs_core_data
  notification-data:
    external: true
    name: edcs_notification_data
  reporting-data:
    external: true
    name: edcs_reporting_data
  events:
    external: true
    name: edcs_events
  smtp-egress: {}

volumes:
  edcs_static:
    external: true
    name: edcs_static

secrets:
  core-django-secret:
    file: ./secrets/core-django-secret
  core-db-password:
    file: ./secrets/core-db-password
  core-relay-db-password:
    file: ./secrets/core-relay-db-password
  notification-db-password:
    file: ./secrets/notification-db-password
  reporting-api-db-password:
    file: ./secrets/reporting-api-db-password
  reporting-projector-db-password:
    file: ./secrets/reporting-projector-db-password
  relay-broker-url:
    file: ./secrets/relay-broker-url
  notification-broker-url:
    file: ./secrets/notification-broker-url
  reporting-broker-url:
    file: ./secrets/reporting-broker-url
  gmail-app-password:
    file: ./secrets/gmail-app-password
  core-reporting-client-credential:
    file: ./secrets/core-reporting-client-credential
  reporting-policy-client-credential:
    file: ./secrets/reporting-policy-client-credential
  database-ca:
    file: ./secrets/database-ca.pem
  broker-ca:
    file: ./secrets/broker-ca.pem
  tls-certificate:
    file: ./secrets/tls-certificate.pem
  tls-private-key:
    file: ./secrets/tls-private-key.pem
```

### 11.4 Limits of this reference

There are deliberately no public database, broker, reporting API or worker ports. The static volume is external and must already contain the matching release's collected assets. The network attachments enforce only the specified reachability; approved runtime/database/broker identities provide the access checks.

depends_on with service_healthy can order startup against an implemented health check, but it does not replace runtime connection retry, current readiness or recovery after a dependency fails. External infrastructure is not managed by depends_on in this file. Workers must retry unavailable broker/database connections using bounded policy. [Compose startup order](https://docs.docker.com/compose/how-tos/startup-order/).

This single-host example does not establish high availability, node failover, multi-host encryption, automatic certificate renewal, resource reservations, log retention or backup recovery. Those must be designed for the chosen production platform. Resource limits and replica counts should follow measured workloads rather than unsupported capacity claims.

## 12. Secrets, identity and authorization configuration

### 12.1 Secret distribution

Grant each service only its required secret mounts. In Compose, service-granted secrets are exposed as files; the application still needs to consume them. File-backed Compose secrets are not an assurance that the source host files are encrypted at rest. Protect source permissions and decide how production secret distribution, rotation and auditing will work. [Compose secrets](https://docs.docker.com/compose/how-tos/use-secrets/).

Do not bake `.env`, initial administrator credentials, SMTP passwords, database URLs or private keys into images. Ensure build context exclusions as well as source-control exclusions. Avoid logging fully interpolated configuration or broker URLs.

### 12.2 Service authentication and delegation

Core-to-reporting and reporting-to-policy calls need authenticated service identity and a validated delegated requester. Before implementation, select the approved mechanism: for example, mutually authenticated transport with bounded signed user assertions, or audience-restricted short-lived service credentials with a verified delegation contract.

This guide does not pretend that a secret filename alone implements that protocol. Define issuer/trust material, audience, expiration, rotation, subject, permitted actions and replay behavior in the contract. Do not accept arbitrary user/branch/Lager IDs from public headers as proof of permission.

### 12.3 Current authorization remains authoritative

The current system combines active user, module action, branch, zonal office, assigned Lager and valid EPF. Financial commands must recheck the current combination. Reporting must apply current scope separately from the age of its data projection.

Configuration should default to deny when required policy validation is unavailable. A cached policy requires an explicitly approved lifetime/revocation design. Authentication at gateway is not enough if an internal service later accepts a foreign resource without checking scope.

### 12.4 Privacy and event payloads

Send only fields needed by the consumer. Notification delivery usually does not need NIC, dates of birth or account history. Reporting may need financial fields but should expose them only under the appropriate View grant and current ownership boundary. Permanent actor/name/EPF snapshots must preserve their meaning without giving every consumer unnecessary personal data.

Keep integration logs, retained events, dead-letter messages and exported files under an agreed retention/access policy. Do not use raw request payloads as an unrestricted event schema.

## 13. Scheduling, health and observability

### 13.1 Interest scheduling stays with the core owner

The current application provides `process_deposit_interest` with optional ended-year posting. The proposed deployment should run that command using the core release and authorized core database identity. It should not give a reporting or notification process permission to post annual interest.

An example of the existing command, shown for future operating documentation only, is:

```bash
python manage.py process_deposit_interest --post-ended
```

The command has not been run for this task. Before scheduling it in another deployment, review unattended-posting authority, historical start-year policy, current rates, closed-year protections and integration-event/audit behavior. Do not leave the original host job active while enabling a second uncontrolled scheduler.

A local flock protects only its host/shared lock context. A multi-host deployment needs a reviewed singleton/fencing design or another approved job coordinator. Account-year uniqueness and posting idempotency remain necessary even when scheduling appears single-instance.

### 13.2 Proposed health contracts

| Component           | Liveness                                        | Readiness or progress evidence                                                                                |
| ------------------- | ----------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| Core web            | Process/request loop responds without mutation. | Database access and required local initialization complete; critical configuration validated.                 |
| Outbox relay        | Relay loop/process remains active.              | Database/broker available, last confirmed publication and oldest unprocessed outbox age.                      |
| Notification worker | Consumer loop/process remains active.           | Broker/database available, last consumed request, oldest due message and failure classification.              |
| Reporting API       | HTTP process responds.                          | Reporting store initialized, policy source reachable according to fail-closed policy, source watermark known. |
| Reporting projector | Consumer loop/process remains active.           | Inbox/projection storage available, last event sequence and lag recorded.                                     |
| Gateway             | Listener/configuration valid.                   | TLS certificate valid and intended upstream route usable.                                                     |

Health checks should not send an actual email, credit an account, close a year or create a synthetic user on every poll. Database/broker loss should be visible; avoid treating every dependency outage as an immediate process crash if controlled recovery is appropriate.

The reference `/app/bin/healthcheck` commands must be implemented inside future images. They are not assumed to exist as routes in the present application.

### 13.3 Required operational metrics

Collect request error/latency by route class, failed authentication, denied policy decisions, outbox depth/oldest age, publisher-confirm failures, consumer lag, dead-letter count, notification retry/failure states, report-job age and financial reconciliation failures. Include deployment version and correlation/operation IDs where appropriate.

Avoid placing member names, NICs, account balances or secret URLs in unrestricted metric labels. High-cardinality personal labels create both privacy and operational problems. Structured logs should distinguish original requester, approver and executing service without pretending all background actions are administrators.

### 13.4 Logging and alerting

Retain sanitized service logs with bounded size/rotation and approved access. Route alerts to the actual operational owner and include a recovery action. Suggested initial warnings are the reporting lag values in the sample configuration, but final thresholds need measured workloads and agreed support hours.

Alert on a stuck outbox even if all containers are healthy. Alert on repeated policy-source failures rather than automatically widening scope. A broker queue with no consumer and a worker that repeatedly rejects the same invalid message should be distinguishable operational states.

### 13.5 Backup and recovery configuration

Back up authoritative core data and necessary owner-local audit/outbox state under an agreed recovery objective. Notification state and report-job metadata may also require backup; derived projection contents should have a defined rebuild path. Protect broker definitions, retained messages and consumer checkpoints according to the selected transport recovery design.

Recovery must establish a consistent source watermark and replay policy. Restoring a core snapshot while consumers keep later inbox/projection state can create divergence unless the restore/reconciliation process handles it. Rebuilding reports should not issue real payments or resend every historical welcome email.

## 14. Future deployment and validation procedure

### 14.1 Readiness gate before any setup

The following is a future implementation checklist. None of these setup actions has been executed by producing this guide.

1. Approve the first extraction scope and the consistency/authorization contracts.
2. Implement and review outbox/inbox models, workers, internal APIs and startup/secret loaders.
3. Build approved immutable images and provide their real references.
4. Provision isolated staging stores, roles, broker topology, external networks/DNS and certificates.
5. Prepare nonsecret env files and service-specific secret distribution.
6. Assign migration, scheduling, monitoring, backup and incident owners.
7. Prepare representative synthetic/staging data and explicit acceptance results.

The current financial core should remain the single authoritative writer throughout the first extraction. Do not split account databases while simply testing notification/reporting deployment.

### 14.2 Configuration validation example

After future deployment files and placeholders have been implemented and reviewed, an operator could validate the Compose structure with a quiet configuration check:

```bash
docker compose --env-file deployment/platform.env \
  -f deployment/compose.yaml config --quiet
```

This command was not run for this task. It validates resolved Compose configuration, not whether service code exists, broker credentials work, permission rules are correct or financial behavior is safe. Avoid dumping a fully resolved secret-bearing configuration to shared logs.

### 14.3 Controlled startup order

Provision and verify databases/broker first. Apply reviewed owner-specific migrations once, populate the static artifact, verify private trust/certificates and establish initial projection snapshots with a source watermark. Start consumers/relay under controlled observation, then core web/reporting API and gateway as appropriate to the cutover plan.

Startup order is not a replacement for idempotent recovery. All components should tolerate a dependency restarting later. Keep the notification/reporting feature routing disabled until shadow results and recovery behavior have been accepted.

An eventual application-layer startup command might be:

```bash
docker compose --env-file deployment/platform.env \
  -f deployment/compose.yaml up -d
```

The example will not work against the present repository because its future deployment files/images/endpoints have not been created. It is included to document the eventual operator workflow, not to request execution.

### 14.4 Staging acceptance checklist

| Check                    | Required outcome                                                                                          |
| ------------------------ | --------------------------------------------------------------------------------------------------------- |
| Host and TLS routing     | Correct public hostname/certificate, HTTP redirect, stable paths and controlled forwarded headers.        |
| Static assets            | Matching release assets load without a development server/CDN dependency.                                 |
| Secret isolation         | Each service can read only the secrets it needs; missing/ambiguous secret configuration fails startup.    |
| Database roles           | Runtime accounts lack cluster administration and cannot write another owner's data.                       |
| Core receipt/closing     | Original transactional and accounting invariants remain unchanged.                                        |
| Outbox crash recovery    | Committed intents are eventually published without invented or missing business facts.                    |
| Consumer redelivery      | Duplicate event IDs do not duplicate projections or business effects.                                     |
| SMTP failure             | Notification state records bounded recovery; committed core data remains intact.                          |
| Reporting reconciliation | Balances, original opening dates, corrections and source watermarks agree with core.                      |
| Access revocation        | Current branch/office/Lager/user changes remove report access despite projection lag.                     |
| Approval application     | A final decision does not report money posted before authoritative application succeeds.                  |
| Scheduler ownership      | Only the approved job path posts ended account-years; duplicate invocation remains safe.                  |
| Backup/rebuild           | Restore and replay recover the intended state without reposting money or resending uncontrolled messages. |
| Release rollback         | Single authoritative writer and durable operation identities remain consistent.                           |

### 14.5 Production readiness

Run deployment/security checks using the intended production profile after a later implementation. Verify actual exposed endpoints, TLS trust, runtime credentials, role grants, policy freshness and operational recovery; a local Compose syntax check alone is insufficient. The Django deployment checklist provides relevant production settings guidance. [Django deployment checklist](https://docs.djangoproject.com/en/6.1/howto/deployment/checklist/).

Set actual service-level and recovery targets with the system owner. No throughput, availability or cost claim is implied by the sample port numbers, retry counts or container structure.

## 15. Troubleshooting and rollback

### 15.1 Common configuration failures

| Symptom                                      | Likely issue and controlled response                                                                                          |
| -------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| Unknown environment variable has no effect   | Current/future settings loader does not consume it; verify the existing-versus-proposed register before changing more values. |
| Application refuses to start without secret  | Supply the intended service secret through the approved loader; do not add a shared hardcoded fallback.                       |
| Container cannot reach database at localhost | localhost is the same container; use the provisioned database service name/endpoint and network attachment.                   |
| External Compose network not found           | Infrastructure/network provisioning has not been completed; the application reference does not create external networks.      |
| Missing static volume/files                  | Static artifacts have not been populated for that release; do not treat the Python listener as the static server.             |
| Redirect loop or insecure generated link     | Proxy protocol trust and Django security configuration disagree; validate the trusted header contract.                        |
| Gmail authentication fails                   | Check account/app-password eligibility, worker's secret loading and configured sender without exposing credentials in logs.   |
| Queue grows while worker appears running     | Inspect broker permissions, consumer binding, database access, last progress and poison-message classification.               |
| Reporting policy lookup fails                | Deny/defer scoped output and repair identity/network/policy configuration; do not switch to unrestricted reads.               |
| Duplicate report totals                      | Check event meaning and inbox identity; a status snapshot may be counted in addition to its linked journal.                   |
| Financial retry adds another entry           | Required business idempotency is missing or keys change between retries; configuration alone cannot repair it.                |
| Two interest jobs run                        | Old host schedule and new scheduler overlap; identify the approved owner before changing scheduling.                          |

### 15.2 Safe rollback boundaries

Notification deployment can be stopped while durable delivery intents remain queued. A reporting read path can revert to the core's authorized reports after reconciling any job/download state. A projection can be rebuilt from an approved source watermark without altering original financial journals.

Do not delete posted receipts, journals, closing records or transfer pages as a deployment rollback. If an authoritative writer has changed, switching routing back requires a data/operation reconciliation plan. Avoid simultaneous old/new writers using unrelated operation identities.

### 15.3 Message recovery

Dead-letter handling needs an authorized operator, reason, source event ID and a classified fix. Replaying a corrected transient message should use its original identity so inbox handling prevents duplicates. A new business operation must have a new identity and pass current permissions; a message replay cannot stand in for new financial approval.

Separate projection rebuilds from notification dispatch. Historical data replay should not trigger every user email again unless a specific, approved communication task requests it.

## 16. Decisions, references and scope confirmation

### 16.1 Decisions before implementation

| Decision                      | Required agreement                                                                     |
| ----------------------------- | -------------------------------------------------------------------------------------- |
| First extraction              | Notifications first and reporting second, or another justified scope.                  |
| Deployment target             | Single-host staging, managed platform or another approved topology.                    |
| Production internal transport | Trusted private connectivity requirements, TLS/mTLS and certificate lifecycle.         |
| Broker and client             | Approved product/version, queue replication, retention and service permissions.        |
| Policy contract               | Delegated requester identity, current scope, audiences, revocation and failure policy. |
| Database layout               | Logical databases versus separate clusters, owner/migration roles and recovery order.  |
| Delivery expectations         | Provider eligibility/limits, SMTP uncertainty, duplicate tolerance and retention.      |
| Reporting guarantees          | Freshness, watermark visibility, cache/export policy and reconciliation.               |
| Scheduling authority          | Approved annual posting path and multi-host singleton/fencing design.                  |
| Operational targets           | Workload, availability, recovery, alerting/support and resource limits.                |
| Implementation authorization  | A separate instruction to create/change application or deployment code.                |

### 16.2 Primary technical references

The source application establishes its current behavior. These vendor references support the proposed configuration concepts and should be rechecked against versions selected during actual implementation.

| Reference                                                                                        | Relevant configuration topic                                             |
| ------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------ |
| [Compose networking](https://docs.docker.com/compose/how-tos/networking/)                        | Service discovery, network boundaries and external networks.             |
| [Compose secrets](https://docs.docker.com/compose/how-tos/use-secrets/)                          | Service-granted file-based secrets and required application consumption. |
| [Compose startup order](https://docs.docker.com/compose/how-tos/startup-order/)                  | Health-check conditions and startup dependency behavior.                 |
| [RabbitMQ acknowledgments and confirms](https://www.rabbitmq.com/docs/confirms)                  | Distinct publisher/consumer delivery responsibilities.                   |
| [Nginx proxy module](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_pass)       | Upstream request path/header behavior.                                   |
| [Django deployment checklist](https://docs.djangoproject.com/en/6.1/howto/deployment/checklist/) | Production secret, debug, HTTPS and deployment validation.               |
| [Gmail SMTP settings](https://support.google.com/mail/answer/7104828?hl=en)                      | Gmail SMTP host and TLS/STARTTLS port.                                   |
| [Google app passwords](https://support.google.com/accounts/answer/185833)                        | Eligible app-password use with 2-Step Verification.                      |

### 16.3 Application evidence and companion document

This guide is aligned with `EDCS_Microservices_Feasibility_Assessment.md`. Its current-state checks use `config/urls.py`, `config/wsgi.py`, `config/asgi.py`, the environment-loading structure in `config/settings.py`, current dependency declarations and the documented business service boundaries.

The financial ownership recommendation follows contribution, Fix Deposit, ledger/transfer, interest and year-end service transactions. The integration prerequisites follow the present local approval replay, request-context audit and on-commit notification design. No environment secret values were included or copied into examples.

### 16.4 Scope of this task

Only this configuration guide and its document formats were created. No operational compose.yaml, environment file, proxy configuration, database role, broker queue, service image, application endpoint or deployment was created or changed. No application tests, database commands, containers, scheduled jobs or emails were run.

The guide provides a concrete configuration direction for later review. Implementation remains a separate task and must preserve the financial, historical and authorization guarantees described in the companion feasibility assessment.

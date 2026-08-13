# dynaconf-ssm-tenant-loader

A [Dynaconf](https://www.dynaconf.com/) custom loader for multi-tenant
applications that store secrets in AWS Systems Manager Parameter Store.

## Path contract

    /<app-prefix>/<env>/app/default
    /<app-prefix>/<env>/app/<variant>
    /<app-prefix>/<env>/tenants/<tenant>/default
    /<app-prefix>/<env>/tenants/<tenant>/<variant>

Tiers are read in that order — least specific first — and **deep-merged**,
so a more specific tier overrides a less specific one key by key rather
than wholesale.

- The **app family** holds values shared by every tenant deployment.
- The **tenant family** holds values specific to one tenant.
- The optional **variant** segment is an opaque deployment discriminator
  (an upstream API major version, a PR review app, ...). It applies to
  both families, so shared-but-variant-specific values have a home.
- The **environment is the outermost segment** because it is the IAM
  boundary: a role granted `/<app-prefix>/<env>/...` can read every
  present and future variant tier without policy changes. Variants come
  and go; grants do not.
- The **`default` leaf is reserved** for the non-variant tier so that no
  tier is a path-prefix of another. A recursive read of one tier can
  therefore never swallow a sibling variant's parameters.
- **Family is the dominant dimension**: a plain tenant value outranks an
  app-plus-variant value. Variant refines a family; it does not escape it.
- A tier that does not exist is simply skipped, so a variant deployment
  still inherits everything from the `default` tenant and app tiers.
- Deeper segments become nested settings:
  `/acme/production/app/default/database/host` → `settings.DATABASE.host`.

> **Migrating from 1.x:** the 1.x contract
> (`/<app-prefix>/app/[<variant>/]<env>`) is not read by this version.
> Re-seed parameters under the new layout and update IAM policies; there
> is no dual-read mode.

### Merge semantics

Deep merge applies per key, at every depth. Given:

    /acme/production/app/default/database/host                      = db.internal
    /acme/production/app/default/database/port                      = @int 5432
    /acme/production/app/default/database/pool/size                 = @int 5
    /acme/production/tenants/tenant-a/default/database/host         = db.tenant-a.internal
    /acme/production/tenants/tenant-a/v2/database/pool/size         = @int 20

a deployment with `tenant=tenant-a`, `variant=v2` resolves to:

```python
settings.DATABASE.host == "db.tenant-a.internal"  # tenant wins
settings.DATABASE.port == 5432  # inherited from app
settings.DATABASE.pool.size == 20  # variant wins, nested
```

Lists **accumulate** rather than replace, per Dynaconf's merge rules: a
list-valued parameter present at two tiers yields the concatenation. Use
Dynaconf's [`@reset`
marker](https://www.dynaconf.com/merging/) on the more specific value to
override instead of extend.

### Variant naming

A variant must be a single path segment and must not collide with the
reserved `default` leaf or a structural segment. The loader rejects,
case-insensitively: `default`, `app`, `tenants`. Anything else is fair
game — with the environment ahead of the variant slot, environment
names can no longer make a path ambiguous, so CI-generated names like
`pr-1234` need no coordination with env naming.

`SSM_PARAMETER_TENANT_VARIANT_FOR_DYNACONF` requires
`SSM_PARAMETER_TENANT_FOR_DYNACONF` to be set, even though it also
affects app-family paths — a variant is a property of a tenant
deployment.

### Single-key loads

`load(..., key="foo")` addresses one leaf parameter via `GetParameter`
and cannot merge: the most specific tier that has it wins outright. It
therefore cannot retrieve a subtree — `key="database"` is a miss even if
`database/host` exists.

## Usage

```python
from dynaconf import Dynaconf

settings = Dynaconf(
    environments=True,
    settings_file="settings.toml",
    LOADERS_FOR_DYNACONF=[
        "dynaconf_ssm_tenant_loader.loader",
        "dynaconf.loaders.env_loader",
    ],
)
```

## Configuration

Set in the process environment (preferred, avoids chicken/egg with
settings files) or in settings:

| Variable | Required | Purpose |
|---|---|---|
| `SSM_PARAMETER_APP_PREFIX_FOR_DYNACONF` | yes | The `<app-prefix>` path segment |
| `SSM_PARAMETER_TENANT_FOR_DYNACONF` | no | Enables the tenant family |
| `SSM_PARAMETER_TENANT_VARIANT_FOR_DYNACONF` | no | Requires tenant; extra path segment |
| `SSM_ENDPOINT_URL_FOR_DYNACONF` | no | e.g. LocalStack |
| `SSM_SESSION_FOR_DYNACONF` | no | Custom `boto3.session.Session` kwargs |

All three path-segment settings are stripped of surrounding whitespace
before use, and the normalized value is written back to the settings
object so `settings.inspect()` agrees with the paths actually queried.
Internal whitespace is an error rather than a silent miss. Tenant and
variant must each be a single path segment; the app prefix may span
several (`acme/team-b`).

## Tenant isolation via IAM

Each tenant deployment should run under its own IAM role (Lambda execution
role, ECS task role, EC2 instance profile, etc.), one per **(tenant,
environment)** pair. Variants need no roles or policy changes of their
own: the grants below cover the `default` leaf and every variant tier.

### Read policy template (per tenant, per environment)

Substitute `<REGION>`, `<ACCOUNT_ID>`, `<APP_PREFIX>`, `<ENV>`, and
`<TENANT>`:

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "AppSharedParameters",
            "Effect": "Allow",
            "Action": [
                "ssm:GetParameter",
                "ssm:GetParameters",
                "ssm:GetParametersByPath"
            ],
            "Resource": "arn:aws:ssm:<REGION>:<ACCOUNT_ID>:parameter/<APP_PREFIX>/<ENV>/app/*"
        },
        {
            "Sid": "OwnTenantParametersOnly",
            "Effect": "Allow",
            "Action": [
                "ssm:GetParameter",
                "ssm:GetParameters",
                "ssm:GetParametersByPath"
            ],
            "Resource": "arn:aws:ssm:<REGION>:<ACCOUNT_ID>:parameter/<APP_PREFIX>/<ENV>/tenants/<TENANT>/*"
        }
    ]
}
```

A single glob per family suffices because every tier path — `default`
and variants alike — nests strictly below `.../app` or
`.../tenants/<TENANT>`. `GetParametersByPath` authorizes against the
path argument (e.g. `/<APP_PREFIX>/<ENV>/app/default`), and
`GetParameter` against the individual parameter ARN; both match the
glob. Unlike bare parent-path grants, this glob is safe: it never
crosses the tenant or environment boundary, because both sit *outside*
it in the path.

**Partial grants are fine.** The loader probes every tier in the
contract, so a role scoped narrower than the template (e.g. app-family
only) will see `AccessDeniedException` on the rest. Those denials are
logged at `WARNING` and skipped — they never fail the load, even with
`silent=False`. Genuine errors (throttling, connectivity) still
propagate when `silent=False`. Denials are indistinguishable from
absent tiers in effect, so if a value mysteriously fails to appear,
check the warnings before checking the parameter names.

Note also that a configured variant doubles the number of
`GetParametersByPath` calls per load, from two to four.

### KMS permissions for SecureString parameters

*(unchanged)*

### Writer/administration policy

*(unchanged — the `/<APP_PREFIX>/*` writer grant covers the new layout
as-is)*

### Warnings

- **Never grant a runtime role `/<APP_PREFIX>`, `/<APP_PREFIX>/<ENV>`,
  or `/<APP_PREFIX>/<ENV>/tenants`.** SSM path permissions are
  hierarchical: a principal allowed to read a parent path can read
  *all* descendants via recursive `GetParametersByPath`, and an
  explicit `Deny` on a child does not reliably block enumeration
  through an allowed parent. The template's two globs are the widest
  safe grants.
- If multiple environments share one AWS account, the `<ENV>` segment
  is what separates them. It is the outermost segment precisely so
  that the per-tenant glob above cannot cross it: a production role
  granted `<ENV>=production` cannot read `staging` parameters, and
  vice versa.
- Tenant and environment names become IAM resource ARN segments. Keep
  them to `a-z0-9-`, and never derive them from untrusted input. The
  loader rejects whitespace outright, since a stray space desyncs the
  path from the grant and surfaces as an empty load rather than an
  authorization error — but it does not police the rest of the
  character set.
- Variants are **not** a security boundary: any code running under a
  (tenant, env) role can read every variant's parameters for that
  tenant and env. If variants are PR review apps executing unreviewed
  code, be deliberate about what the shared `default` tiers contain.

## Development

### Environment setup

The project uses [uv](https://docs.astral.sh/uv/) for dependency
management. Either use uv directly:

```sh
uv sync
```

or use the provided Nix flake, which supplies a pinned Python
interpreter, `uv`, and `ruff`, and syncs the environment on shell entry:

```sh
nix develop
```

The flake pins `uv` to the Nix-provided interpreter
(`UV_PYTHON_DOWNLOADS=never`), so no standalone Python builds are downloaded.
The virtual environment is created in-project at `.venv/`.

### direnv

If you use [direnv](https://direnv.net/), create a `.envrc` in the
project root:

```sh
use flake
dotenv_if_exists .env
```

Then allow it once:

```sh
direnv allow
```

- `use flake` activates the Nix development shell automatically whenever
  you enter the directory (requires
  [nix-direnv](https://github.com/nix-community/nix-direnv) for caching,
  strongly recommended).
- `dotenv_if_exists .env` loads a local, untracked `.env` file into your
  shell if one is present, and is silently skipped otherwise. This is
  useful for local experimentation against real or emulated AWS, e.g.:

  ```sh
  # .env — local only, never commit
  AWS_PROFILE=my-dev-profile
  AWS_DEFAULT_REGION=us-east-1
  SSM_PARAMETER_APP_PREFIX_FOR_DYNACONF=acme
  SSM_PARAMETER_TENANT_FOR_DYNACONF=tenant-a
  ```

Ensure `.env` is in `.gitignore` — it may contain credentials and must
never be committed. (`.envrc` itself contains no secrets and is safe to
commit.)

Note that the test suite does **not** need any of these variables: the
`aws_credentials` fixture sets fake credentials and clears the loader's
environment variables so host configuration cannot leak into tests.

### Linting and formatting

[Ruff](https://docs.astral.sh/ruff/) handles both:

```sh
uv run ruff check .
uv run ruff format --check .
```

Inside the Nix shell, `ruff` is on `PATH` directly, so plain
`ruff check .` also works.

### Running tests

```sh
uv run pytest

# or, without entering a shell:
nix run .#test

# pass pytest arguments through:
nix run .#test -- -k tenant -v
```

Tests use [`moto`](https://github.com/getmoto/moto) to mock AWS SSM — no
Docker, LocalStack, or AWS account required.

## Releasing

The canonical version lives in `pyproject.toml`; the package exposes it
at runtime via `importlib.metadata`, so it is bumped in exactly one
place.

1. Update `version` in `pyproject.toml` and note the changes in the
   changelog.
2. Commit and tag:

   ```sh
   git commit -am "Release v0.2.0"
   git tag -a v0.2.0 -m "v0.2.0"
   git push --follow-tags
   ```

3. Build the sdist and wheel:

   ```sh
   uv build
   ```

   Artifacts are written to `dist/`.

4. Publish to PyPI:

   ```sh
   uv publish
   ```

   `uv publish` reads credentials from `UV_PUBLISH_TOKEN` (a PyPI API
   token). For CI-driven publishing, prefer PyPI
   [trusted publishing](https://docs.pypi.org/trusted-publishers/) from
   a GitHub Actions workflow triggered on the tag, which removes the
   need for a long-lived token entirely.

5. Verify the release installs cleanly:

   ```sh
   uv run --with dynaconf-ssm-tenant-loader --no-project \
       python -c "import dynaconf_ssm_tenant_loader as m; print(m.__version__)"
   ```

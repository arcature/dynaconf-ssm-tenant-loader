# dynaconf-ssm-tenant-loader

A [Dynaconf](https://www.dynaconf.com/) custom loader for multi-tenant
applications that store secrets in AWS Systems Manager Parameter Store.

## Path contract

    /<app-prefix>/app/<env>/<parameter>
    /<app-prefix>/tenants/<tenant>[/<variant>]/<env>/<parameter>

- The **app family** holds values shared by every tenant deployment.
- The **tenant family** holds values specific to one tenant.
- Tenant values are loaded second and **override** app values on conflict.
- The optional **variant** segment is an opaque deployment discriminator
  (e.g. an upstream API major version) interposed between tenant and env.
- Deeper segments become nested settings:
  `/acme/app/production/database/host` → `settings.DATABASE.host`.

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

## Tenant isolation via IAM

Each tenant deployment should run under its own IAM role (Lambda execution
role, ECS task role, EC2 instance profile, etc.). Grant that role **only**:

1. The app-family path for its environment.
2. Its own tenant-family leaf path for its environment.

### Read policy template (per tenant, per environment)

Substitute `<REGION>`, `<ACCOUNT_ID>`, `<APP_PREFIX>`, `<TENANT>`,
`<VARIANT>` (omit the segment entirely if unused), and `<ENV>`:

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
            "Resource": [
                "arn:aws:ssm:<REGION>:<ACCOUNT_ID>:parameter/<APP_PREFIX>/app/<ENV>",
                "arn:aws:ssm:<REGION>:<ACCOUNT_ID>:parameter/<APP_PREFIX>/app/<ENV>/*"
            ]
        },
        {
            "Sid": "OwnTenantParametersOnly",
            "Effect": "Allow",
            "Action": [
                "ssm:GetParameter",
                "ssm:GetParameters",
                "ssm:GetParametersByPath"
            ],
            "Resource": [
                "arn:aws:ssm:<REGION>:<ACCOUNT_ID>:parameter/<APP_PREFIX>/tenants/<TENANT>/<VARIANT>/<ENV>",
                "arn:aws:ssm:<REGION>:<ACCOUNT_ID>:parameter/<APP_PREFIX>/tenants/<TENANT>/<VARIANT>/<ENV>/*"
            ]
        }
    ]
}
```

Both the bare path and the `/*` glob are listed: `GetParametersByPath`
authorizes against the path argument itself, while `GetParameter` on a
child authorizes against the individual parameter ARN.

### KMS permissions for SecureString parameters

`SecureString` values encrypted with the AWS-managed key (`aws/ssm`)
require no extra statement. If you use a customer-managed KMS key, the
role also needs:

```json
{
    "Sid": "DecryptSecureStrings",
    "Effect": "Allow",
    "Action": "kms:Decrypt",
    "Resource": "arn:aws:kms:<REGION>:<ACCOUNT_ID>:key/<KEY_ID>"
}
```

For defense in depth, encrypt each tenant's parameters with a
**per-tenant** customer-managed key and scope each role's `kms:Decrypt`
to its own key. Then even a misconfigured SSM grant yields only
ciphertext.

### Writer/administration policy

Runtime roles never write parameters. Seeding and rotation should be
done by a separate CI or administrative principal, e.g.:

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "ManageAppParameterTree",
            "Effect": "Allow",
            "Action": [
                "ssm:PutParameter",
                "ssm:DeleteParameter",
                "ssm:DeleteParameters",
                "ssm:GetParameter",
                "ssm:GetParameters",
                "ssm:GetParametersByPath",
                "ssm:DescribeParameters",
                "ssm:AddTagsToResource",
                "ssm:ListTagsForResource"
            ],
            "Resource": "arn:aws:ssm:<REGION>:<ACCOUNT_ID>:parameter/<APP_PREFIX>/*"
        }
    ]
}
```

This principal must not be assumable by tenant runtime roles.

> **Note:** `ssm:DescribeParameters` only supports `Resource: "*"` in
> some contexts and reveals parameter *names* (not values) across the
> account. It is not required by this loader at runtime — the loader
> uses only `GetParameter` and `GetParametersByPath` — so leave it off
> runtime roles.

### Warnings

- **Never grant a runtime role `/<APP_PREFIX>` or
  `/<APP_PREFIX>/tenants`.** SSM path permissions are hierarchical: a
  principal allowed to read a parent path can read *all* descendants
  via recursive `GetParametersByPath`, and an explicit `Deny` on a
  child does not reliably block enumeration through an allowed parent.
  Always allow-list leaf paths only, as in the template above.
- Tenant and environment names become IAM resource ARN segments; keep
  them free of characters requiring escaping (`a-z0-9-` is a safe set)
  and never derive them from untrusted input.
- If multiple environments share one AWS account, the `<ENV>` segment
  in the resource ARN is what separates them — a production role
  granted `<ENV>=production` cannot read `staging` parameters, and
  vice versa.


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
## Testing

Tests use [`moto`](https://github.com/getmoto/moto) to mock AWS SSM — no
Docker, LocalStack, or AWS account is required.

### With uv

```sh
uv sync
uv run pytest
```

### With Nix

A `flake.nix` is provided. It supplies a pinned Python interpreter, `uv`,
and `ruff`, and syncs the project environment on shell entry:

```sh
# Enter a development shell (runs `uv sync` automatically):
nix develop
uv run pytest

# Or run the test suite directly, without entering a shell:
nix run .#test

# Pass pytest arguments through:
nix run .#test -- -k tenant -v
```

The flake pins `uv` to the Nix-provided interpreter
(`UV_PYTHON_DOWNLOADS=never`), so no standalone Python builds are
downloaded. The virtual environment is created in-project at `.venv/`.

If you use [direnv](https://direnv.net/), a one-line `.envrc` gets you
automatic shell activation:

```sh
echo "use flake" > .envrc && direnv allow
```

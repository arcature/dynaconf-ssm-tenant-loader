"""
Custom Dynaconf loader for multi-tenant configuration in AWS Systems
Manager Parameter Store.

Path contract (least to most specific):

    /<app-prefix>/app/<env>
    /<app-prefix>/app/<variant>/<env>
    /<app-prefix>/tenants/<tenant>/<env>
    /<app-prefix>/tenants/<tenant>/<variant>/<env>

Tiers are loaded in that order and deep-merged, so more specific tiers
override less specific ones key by key.
"""

from __future__ import annotations

import logging
import os
import typing as t

import boto3
from botocore.exceptions import BotoCoreError, ClientError, NoRegionError
from dynaconf.utils.parse_conf import parse_conf_data

from . import generate_loader_identifier
from .util import pull_from_env_or_obj, slashes_to_dict

if t.TYPE_CHECKING:
    from types_boto3_ssm import SSMClient


logger = logging.getLogger("dynaconf.ssm_tenant_loader")

APP_PREFIX_KEY = "SSM_PARAMETER_APP_PREFIX_FOR_DYNACONF"
TENANT_KEY = "SSM_PARAMETER_TENANT_FOR_DYNACONF"
VARIANT_KEY = "SSM_PARAMETER_TENANT_VARIANT_FOR_DYNACONF"

#: Names a variant may not take, because they would collide with a
#: structural segment or with an environment tier and make a path
#: ambiguous under recursive reads.
RESERVED_VARIANT_NAMES = frozenset(
    {
        "app",
        "tenants",
        "default",
        "global",
        "dev",
        "development",
        "stage",
        "staging",
        "prod",
        "production",
        "test",
        "testing",
    }
)

#: Client error codes treated as "this tier is simply not available to
#: me", rather than as a failure, even when ``silent=False``. With four
#: tiers, partial IAM grants are the norm.
ACCESS_DENIED_CODES = frozenset({"AccessDenied", "AccessDeniedException"})


def get_client(obj) -> SSMClient:
    """Build a boto3 SSM client from optional loader settings."""

    endpoint_url = obj.get("SSM_ENDPOINT_URL_FOR_DYNACONF")
    session = boto3.session.Session(**obj.get("SSM_SESSION_FOR_DYNACONF", {}))
    return session.client(service_name="ssm", endpoint_url=endpoint_url)


def validate_variant(variant: str, env_name: str) -> None:
    """
    Reject variants that would produce an ambiguous path.

    :param variant: the configured variant segment
    :param env_name: the environment currently being loaded
    :raises ValueError: if the variant is empty, contains a slash, or
        collides with an environment or structural path segment
    """

    if not variant.strip():
        raise ValueError(f"{VARIANT_KEY} must not be empty or whitespace.")

    if "/" in variant:
        raise ValueError(
            f"{VARIANT_KEY} is a single path segment and must not contain '/'."
        )

    normalized_variant = variant.strip().lower()
    env_normalized = env_name.strip().lower()

    if (
        normalized_variant == env_normalized
        or normalized_variant in RESERVED_VARIANT_NAMES
    ):
        raise ValueError(
            f"{VARIANT_KEY}={variant!r} collides with an environment or"
            " structural path segment, which would make the parameter"
            " path ambiguous under a recursive read. Choose a variant"
            " name that is not one of"
            f" {sorted(RESERVED_VARIANT_NAMES | {env_normalized})}."
        )


def build_paths(
    app_prefix: str,
    env_name: str,
    tenant: str | None = None,
    variant: str | None = None,
) -> list[str]:
    """
    Build the ordered list of SSM base paths to load, least specific
    first, so that later tiers win on merge.

    Family is the dominant dimension: a plain tenant value outranks an
    app-plus-variant value.
    """

    families = [[app_prefix, "app"]]

    if tenant is not None:
        families.append([app_prefix, "tenants", tenant])

    paths = []

    for family in families:
        paths.append("/" + "/".join([*family, env_name]))
        if variant is not None:
            paths.append("/" + "/".join([*family, variant, env_name]))

    return paths


def load(
    obj,
    env: str | None = None,
    silent: bool = True,
    key: str | None = None,
    validate: bool = False,
) -> None:
    """
    Read and load into ``obj`` a single key or all keys from the
    Parameter Store source.

    :param obj: the settings instance
    :param env: settings current env; defaults to ``obj.current_env``
    :param silent: if False, connection/lookup errors raise; access
        denials are always soft
    :param key: if defined, load a single key; else load all for ``env``
    :param validate: whether loaded data is validated when set on ``obj``
    """

    app_prefix = pull_from_env_or_obj(APP_PREFIX_KEY, os.environ, obj)
    if app_prefix is None:
        raise ValueError(
            f"{APP_PREFIX_KEY} must be set in settings or environment"
            " for the SSM tenant loader to function."
        )

    tenant = pull_from_env_or_obj(TENANT_KEY, os.environ, obj)
    variant = pull_from_env_or_obj(VARIANT_KEY, os.environ, obj)

    if variant is not None and tenant is None:
        raise ValueError(
            f"{VARIANT_KEY} is set but {TENANT_KEY} is not; a tenant"
            " variant is only meaningful alongside a tenant."
        )

    env_name = (env or obj.current_env).strip().lower()

    if variant is not None:
        variant = variant.strip()
        validate_variant(variant, env_name)

        # `pull_from_env_or_obj` from above may have stored the raw value, which
        # no longer matches the paths used, so store the normalized value back
        # on the settings object.
        obj.set(VARIANT_KEY, variant)

    try:
        client = get_client(obj)
    except NoRegionError:
        if silent:
            logger.exception(
                "An AWS region must be available for the Dynaconf SSM"
                " tenant loader to function."
            )
            return
        raise

    paths = build_paths(app_prefix, env_name, tenant, variant)

    if key is not None:
        # Single-key mode addresses one leaf parameter and cannot merge,
        # so the most specific tier that has it wins outright.
        for path in reversed(paths):
            value = _fetch_single_parameter(client, path, key, silent=silent)
            if value is not None:
                obj.set(key, value, validate=validate)
                return
        return

    for path in paths:
        results = _fetch_all_parameters(client, path, silent=silent)
        if results:
            obj.update(
                results,
                loader_identifier=generate_loader_identifier(path, env),
                validate=validate,
                merge=True,
            )


def _handle_client_error(
    exc: ClientError,
    path: str,
    silent: bool,
) -> None:
    """
    Decide what to do with a ``ClientError`` raised while reading a tier.

    Returns normally if the caller should treat the tier as absent;
    re-raises otherwise.
    """

    code = exc.response.get("Error", {}).get("Code")

    if code == "ParameterNotFound":
        logger.debug("Parameter %s does not exist in AWS SSM.", path)
        return

    if code in ACCESS_DENIED_CODES:
        logger.warning(
            "Access denied reading %s from AWS SSM; skipping this tier."
            " This is expected when the role is scoped to a subset of"
            " the path contract.",
            path,
        )
        return

    if silent:
        return

    raise exc


def _fetch_single_parameter(
    client,
    base_path: str,
    key: str,
    silent: bool = True,
):
    """Fetch a single parameter beneath ``base_path``."""

    path = f"{base_path}/{key}"
    logger.debug("Attempting to load parameter %s from AWS SSM", path)

    try:
        response = client.get_parameter(Name=path, WithDecryption=True)
    except ClientError as exc:
        _handle_client_error(exc, path, silent)
        return None
    except BotoCoreError:
        if silent:
            return None
        logger.error(
            "Could not connect to AWS SSM at endpoint %s.",
            client.meta.endpoint_url,
        )
        raise

    if data := response.get("Parameter"):
        return parse_conf_data(data["Value"], tomlfy=True)

    return None


def _fetch_all_parameters(
    client,
    base_path: str,
    silent: bool = True,
):
    """Recursively fetch all parameters beneath ``base_path``."""

    logger.debug("Loading all parameters from AWS SSM for path %s", base_path)

    data: dict[str, str] = {}
    paginator = client.get_paginator("get_parameters_by_path")

    try:
        for page in paginator.paginate(
            Path=base_path, Recursive=True, WithDecryption=True
        ):
            for parameter in page["Parameters"]:
                relative_key = parameter["Name"].removeprefix(base_path).strip("/")
                data[relative_key] = parameter["Value"]
    except ClientError as exc:
        _handle_client_error(exc, base_path, silent)
        return None
    except BotoCoreError:
        if silent:
            return None
        logger.error(
            "Could not connect to AWS SSM at endpoint %s.",
            client.meta.endpoint_url,
        )
        raise

    if not data:
        return None

    return parse_conf_data(slashes_to_dict(data), tomlfy=True)

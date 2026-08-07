"""
Custom Dynaconf loader for multi-tenant configuration in AWS Systems
Manager Parameter Store.

Path contract:

    /<app-prefix>/app/<env>/<parameter>
    /<app-prefix>/tenants/<tenant>[/<variant>]/<env>/<parameter>

App-family parameters are loaded first; tenant-family parameters are
loaded second and take precedence on conflicting keys.
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


def get_client(obj) -> SSMClient:
    """Build a boto3 SSM client from optional loader settings."""

    endpoint_url = obj.get("SSM_ENDPOINT_URL_FOR_DYNACONF")
    session = boto3.session.Session(**obj.get("SSM_SESSION_FOR_DYNACONF", {}))
    return session.client(service_name="ssm", endpoint_url=endpoint_url)


def build_paths(
    app_prefix: str,
    env_name: str,
    tenant: str | None,
    variant: str | None,
) -> list[str]:
    """
    Build the ordered list of SSM base paths to load. App family first,
    tenant family (if a tenant is configured) second, so tenant values
    win on merge.
    """

    paths = [f"/{app_prefix}/app/{env_name}"]

    if tenant is not None:
        segments = [app_prefix, "tenants", tenant]
        if variant is not None:
            segments.append(variant)
        segments.append(env_name)
        paths.append("/" + "/".join(segments))

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
    :param silent: if False, connection/lookup errors raise
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

    env_name = (env or obj.current_env).strip().lower()
    paths = build_paths(app_prefix, env_name, tenant, variant)

    if key is not None:
        # Single-key mode: most specific source wins, so search the
        # tenant family first and fall back to the app family.
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
            )


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
        if exc.response.get("Error", {}).get("Code") == "ParameterNotFound":
            logger.debug("Parameter %s does not exist in AWS SSM.", path)
            return None
        if silent:
            return None
        raise
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
    except ClientError:
        if silent:
            return None
        raise
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

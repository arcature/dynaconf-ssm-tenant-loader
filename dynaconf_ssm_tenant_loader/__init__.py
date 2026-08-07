"""
Multi-tenant AWS SSM Parameter Store loader for Dynaconf.
"""

from importlib.metadata import version

from dynaconf.loaders.base import SourceMetadata

__version__ = version("dynaconf-ssm-tenant-loader")


def generate_loader_identifier(path: str, env: str | None = None):
    """
    Identify the provenance of loaded key/value pairs on the ``Settings``
    object, so that ``settings.inspect()`` can distinguish app-shared
    values from tenant-specific values.

    :param path: The SSM path the values were sourced from.
    :param env: The environment name in effect during the load.
    """
    # `global` is the default for Dynaconf loaders, so use that if no env is
    # provided.
    env = env or "global"
    return SourceMetadata(loader="ssm-tenant", identifier=path, env=env)

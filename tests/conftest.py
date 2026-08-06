import boto3
import pytest
from dynaconf import Dynaconf
from moto import mock_aws


@pytest.fixture
def aws_credentials(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    # Ensure loader config from the host environment can't leak in.
    for var in (
        "SSM_PARAMETER_APP_PREFIX_FOR_DYNACONF",
        "SSM_PARAMETER_TENANT_FOR_DYNACONF",
        "SSM_PARAMETER_TENANT_VARIANT_FOR_DYNACONF",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def ssm(aws_credentials):
    with mock_aws():
        yield boto3.client("ssm", region_name="us-east-1")


@pytest.fixture
def put_params(ssm):
    """Seed a mapping of full SSM paths to values."""

    def _put(params: dict[str, str], type: str = "String"):
        for name, value in params.items():
            ssm.put_parameter(Name=name, Value=value, Type=type)

    return _put


@pytest.fixture
def settings():
    """A bare Dynaconf instance with no automatic loaders."""

    return Dynaconf(
        environments=True,
        LOADERS_FOR_DYNACONF=[],
        ENV_FOR_DYNACONF="production",
    )

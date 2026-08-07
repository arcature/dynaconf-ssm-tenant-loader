import pytest

from dynaconf_ssm_tenant_loader import loader


def test_app_family_loads_without_a_tenant(settings, put_params):
    put_params({"/acme/app/production/flask_secret_key": "app-secret"})
    settings.set("SSM_PARAMETER_APP_PREFIX_FOR_DYNACONF", "acme")

    loader.load(settings, env="production")

    assert settings.FLASK_SECRET_KEY == "app-secret"


def test_tenant_value_overrides_app_value(settings, put_params):
    put_params(
        {
            "/acme/app/production/api_key": "shared-key",
            "/acme/tenants/tenant-a/production/api_key": "tenant-a-key",
        }
    )
    settings.set("SSM_PARAMETER_APP_PREFIX_FOR_DYNACONF", "acme")
    settings.set("SSM_PARAMETER_TENANT_FOR_DYNACONF", "tenant-a")

    loader.load(settings, env="production")

    assert settings.API_KEY == "tenant-a-key"


def test_variant_segment_is_interposed_between_tenant_and_env(settings, put_params):
    put_params({"/acme/tenants/tenant-a/v2/production/api_password": "sekrit"})
    settings.set("SSM_PARAMETER_APP_PREFIX_FOR_DYNACONF", "acme")
    settings.set("SSM_PARAMETER_TENANT_FOR_DYNACONF", "tenant-a")
    settings.set("SSM_PARAMETER_TENANT_VARIANT_FOR_DYNACONF", "v2")

    loader.load(settings, env="production")

    assert settings.API_PASSWORD == "sekrit"


def test_other_tenant_parameters_are_not_loaded(settings, put_params):
    put_params(
        {
            "/acme/tenants/tenant-a/production/api_key": "tenant-a-key",
            "/acme/tenants/tenant-b/production/api_key": "tenant-b-key",
        }
    )
    settings.set("SSM_PARAMETER_APP_PREFIX_FOR_DYNACONF", "acme")
    settings.set("SSM_PARAMETER_TENANT_FOR_DYNACONF", "tenant-a")

    loader.load(settings, env="production")

    assert settings.API_KEY == "tenant-a-key"


def test_nested_paths_become_nested_dicts(settings, put_params):
    put_params({"/acme/app/production/database/host": "db.example.com"})
    settings.set("SSM_PARAMETER_APP_PREFIX_FOR_DYNACONF", "acme")

    loader.load(settings, env="production")

    assert settings.DATABASE.host == "db.example.com"


def test_values_are_toml_coerced(settings, put_params):
    put_params(
        {
            "/acme/app/production/max_connections": "@int 42",
            "/acme/app/production/debug": "@bool false",
        }
    )
    settings.set("SSM_PARAMETER_APP_PREFIX_FOR_DYNACONF", "acme")

    loader.load(settings, env="production")

    assert settings.MAX_CONNECTIONS == 42
    assert settings.DEBUG is False


def test_secure_strings_are_decrypted(settings, put_params):
    put_params(
        {"/acme/app/production/stripe_secret_key": "sk_live_abc"},
        type="SecureString",
    )
    settings.set("SSM_PARAMETER_APP_PREFIX_FOR_DYNACONF", "acme")

    loader.load(settings, env="production")

    assert settings.STRIPE_SECRET_KEY == "sk_live_abc"


def test_single_key_prefers_tenant_over_app(settings, put_params):
    put_params(
        {
            "/acme/app/production/webhook_secret": "app-level",
            "/acme/tenants/tenant-a/production/webhook_secret": "tenant-level",
        }
    )
    settings.set("SSM_PARAMETER_APP_PREFIX_FOR_DYNACONF", "acme")
    settings.set("SSM_PARAMETER_TENANT_FOR_DYNACONF", "tenant-a")

    loader.load(settings, env="production", key="webhook_secret")

    assert settings.WEBHOOK_SECRET == "tenant-level"


def test_single_key_falls_back_to_app_family(settings, put_params):
    put_params({"/acme/app/production/webhook_secret": "app-level"})
    settings.set("SSM_PARAMETER_APP_PREFIX_FOR_DYNACONF", "acme")
    settings.set("SSM_PARAMETER_TENANT_FOR_DYNACONF", "tenant-a")

    loader.load(settings, env="production", key="webhook_secret")

    assert settings.WEBHOOK_SECRET == "app-level"


def test_missing_paths_are_silent_by_default(settings, ssm):
    settings.set("SSM_PARAMETER_APP_PREFIX_FOR_DYNACONF", "acme")

    loader.load(settings, env="production")  # nothing seeded; must not raise


def test_missing_app_prefix_raises(settings, ssm):
    with pytest.raises(ValueError, match="SSM_PARAMETER_APP_PREFIX_FOR_DYNACONF"):
        loader.load(settings, env="production")


def test_variant_without_tenant_raises(settings, ssm):
    settings.set("SSM_PARAMETER_APP_PREFIX_FOR_DYNACONF", "acme")
    settings.set("SSM_PARAMETER_TENANT_VARIANT_FOR_DYNACONF", "v2")

    with pytest.raises(ValueError, match="TENANT_VARIANT"):
        loader.load(settings, env="production")


def test_loader_config_is_read_from_environment(settings, put_params, monkeypatch):
    put_params({"/acme/tenants/tenant-a/production/api_key": "from-env-config"})
    monkeypatch.setenv("SSM_PARAMETER_APP_PREFIX_FOR_DYNACONF", "acme")
    monkeypatch.setenv("SSM_PARAMETER_TENANT_FOR_DYNACONF", "tenant-a")

    loader.load(settings, env="production")

    assert settings.API_KEY == "from-env-config"

import pytest
from botocore.exceptions import ClientError

from dynaconf_ssm_tenant_loader import loader


def test_app_family_loads_without_a_tenant(settings, put_params):
    put_params({"/acme/production/app/default/flask_secret_key": "app-secret"})
    settings.set("SSM_PARAMETER_APP_PREFIX_FOR_DYNACONF", "acme")

    loader.load(settings, env="production")

    assert settings.FLASK_SECRET_KEY == "app-secret"


def test_tenant_value_overrides_app_value(settings, put_params):
    put_params(
        {
            "/acme/production/app/default/api_key": "shared-key",
            "/acme/production/tenants/tenant-a/default/api_key": "tenant-a-key",
        }
    )
    settings.set("SSM_PARAMETER_APP_PREFIX_FOR_DYNACONF", "acme")
    settings.set("SSM_PARAMETER_TENANT_FOR_DYNACONF", "tenant-a")

    loader.load(settings, env="production")

    assert settings.API_KEY == "tenant-a-key"


def test_variant_tier_loads(settings, put_params):
    put_params({"/acme/production/tenants/tenant-a/v2/api_password": "sekrit"})
    settings.set("SSM_PARAMETER_APP_PREFIX_FOR_DYNACONF", "acme")
    settings.set("SSM_PARAMETER_TENANT_FOR_DYNACONF", "tenant-a")
    settings.set("SSM_PARAMETER_TENANT_VARIANT_FOR_DYNACONF", "v2")

    loader.load(settings, env="production")

    assert settings.API_PASSWORD == "sekrit"


def test_other_tenant_parameters_are_not_loaded(settings, put_params):
    put_params(
        {
            "/acme/production/tenants/tenant-a/default/api_key": "tenant-a-key",
            "/acme/production/tenants/tenant-b/default/api_key": "tenant-b-key",
        }
    )
    settings.set("SSM_PARAMETER_APP_PREFIX_FOR_DYNACONF", "acme")
    settings.set("SSM_PARAMETER_TENANT_FOR_DYNACONF", "tenant-a")

    loader.load(settings, env="production")

    assert settings.API_KEY == "tenant-a-key"


def test_variant_parameters_are_not_loaded_without_a_variant(settings, put_params):
    """
    A variant tier is a sibling of the ``default`` leaf, not a subtree
    of it, so a non-variant deployment must not see variant values —
    neither merged in nor as junk nested keys.
    """
    put_params(
        {
            "/acme/production/app/default/api_key": "app-key",
            "/acme/production/app/v2/api_key": "v2-key",
            "/acme/production/tenants/tenant-a/default/who": "tenant",
            "/acme/production/tenants/tenant-a/v2/who": "variant",
        }
    )
    settings.set("SSM_PARAMETER_APP_PREFIX_FOR_DYNACONF", "acme")
    settings.set("SSM_PARAMETER_TENANT_FOR_DYNACONF", "tenant-a")

    loader.load(settings, env="production")

    assert settings.API_KEY == "app-key"
    assert settings.WHO == "tenant"
    assert "V2" not in settings


def test_nested_paths_become_nested_dicts(settings, put_params):
    put_params({"/acme/production/app/default/database/host": "db.example.com"})
    settings.set("SSM_PARAMETER_APP_PREFIX_FOR_DYNACONF", "acme")

    loader.load(settings, env="production")

    assert settings.DATABASE.host == "db.example.com"


def test_values_are_toml_coerced(settings, put_params):
    put_params(
        {
            "/acme/production/app/default/max_connections": "@int 42",
            "/acme/production/app/default/debug": "@bool false",
        }
    )
    settings.set("SSM_PARAMETER_APP_PREFIX_FOR_DYNACONF", "acme")

    loader.load(settings, env="production")

    assert settings.MAX_CONNECTIONS == 42
    assert settings.DEBUG is False


def test_secure_strings_are_decrypted(settings, put_params):
    put_params(
        {"/acme/production/app/default/stripe_secret_key": "sk_live_abc"},
        type="SecureString",
    )
    settings.set("SSM_PARAMETER_APP_PREFIX_FOR_DYNACONF", "acme")

    loader.load(settings, env="production")

    assert settings.STRIPE_SECRET_KEY == "sk_live_abc"


def test_single_key_prefers_tenant_over_app(settings, put_params):
    put_params(
        {
            "/acme/production/app/default/webhook_secret": "app-level",
            "/acme/production/tenants/tenant-a/default/webhook_secret": "tenant-level",
        }
    )
    settings.set("SSM_PARAMETER_APP_PREFIX_FOR_DYNACONF", "acme")
    settings.set("SSM_PARAMETER_TENANT_FOR_DYNACONF", "tenant-a")

    loader.load(settings, env="production", key="webhook_secret")

    assert settings.WEBHOOK_SECRET == "tenant-level"


def test_single_key_falls_back_to_app_family(settings, put_params):
    put_params({"/acme/production/app/default/webhook_secret": "app-level"})
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
    put_params({"/acme/production/tenants/tenant-a/default/api_key": "from-env-config"})
    monkeypatch.setenv("SSM_PARAMETER_APP_PREFIX_FOR_DYNACONF", "acme")
    monkeypatch.setenv("SSM_PARAMETER_TENANT_FOR_DYNACONF", "tenant-a")

    loader.load(settings, env="production")

    assert settings.API_KEY == "from-env-config"


def test_variant_tier_falls_back_to_plain_tenant_tier(settings, put_params):
    put_params(
        {
            "/acme/production/tenants/tenant-a/default/only_at_tenant": "tenant-level",
            "/acme/production/tenants/tenant-a/v2/only_at_variant": "variant-level",
        }
    )
    settings.set("SSM_PARAMETER_APP_PREFIX_FOR_DYNACONF", "acme")
    settings.set("SSM_PARAMETER_TENANT_FOR_DYNACONF", "tenant-a")
    settings.set("SSM_PARAMETER_TENANT_VARIANT_FOR_DYNACONF", "v2")

    loader.load(settings, env="production")

    assert settings.ONLY_AT_TENANT == "tenant-level"
    assert settings.ONLY_AT_VARIANT == "variant-level"


def test_full_precedence_ladder(settings, put_params):
    put_params(
        {
            "/acme/production/app/default/who": "app",
            "/acme/production/app/v2/who": "app-variant",
            "/acme/production/tenants/tenant-a/default/who": "tenant",
            "/acme/production/tenants/tenant-a/v2/who": "tenant-variant",
        }
    )
    settings.set("SSM_PARAMETER_APP_PREFIX_FOR_DYNACONF", "acme")
    settings.set("SSM_PARAMETER_TENANT_FOR_DYNACONF", "tenant-a")
    settings.set("SSM_PARAMETER_TENANT_VARIANT_FOR_DYNACONF", "v2")

    loader.load(settings, env="production")

    assert settings.WHO == "tenant-variant"


def test_family_outranks_variant(settings, put_params):
    put_params(
        {
            "/acme/production/app/v2/who": "app-variant",
            "/acme/production/tenants/tenant-a/default/who": "tenant",
        }
    )
    settings.set("SSM_PARAMETER_APP_PREFIX_FOR_DYNACONF", "acme")
    settings.set("SSM_PARAMETER_TENANT_FOR_DYNACONF", "tenant-a")
    settings.set("SSM_PARAMETER_TENANT_VARIANT_FOR_DYNACONF", "v2")

    loader.load(settings, env="production")

    assert settings.WHO == "tenant"


def test_app_variant_tier_loads_without_tenant_values(settings, put_params):
    put_params({"/acme/production/app/v2/upstream_base_url": "https://v2.example.com"})
    settings.set("SSM_PARAMETER_APP_PREFIX_FOR_DYNACONF", "acme")
    settings.set("SSM_PARAMETER_TENANT_FOR_DYNACONF", "tenant-a")
    settings.set("SSM_PARAMETER_TENANT_VARIANT_FOR_DYNACONF", "v2")

    loader.load(settings, env="production")

    assert settings.UPSTREAM_BASE_URL == "https://v2.example.com"


def test_nested_dicts_are_deep_merged_across_tiers(settings, put_params):
    put_params(
        {
            "/acme/production/app/default/database/host": "db.internal",
            "/acme/production/app/default/database/port": "@int 5432",
            "/acme/production/app/default/database/pool/size": "@int 5",
            "/acme/production/tenants/tenant-a/default/database/host": (
                "db.tenant-a.internal"
            ),
            "/acme/production/tenants/tenant-a/v2/database/pool/size": "@int 20",
        }
    )
    settings.set("SSM_PARAMETER_APP_PREFIX_FOR_DYNACONF", "acme")
    settings.set("SSM_PARAMETER_TENANT_FOR_DYNACONF", "tenant-a")
    settings.set("SSM_PARAMETER_TENANT_VARIANT_FOR_DYNACONF", "v2")

    loader.load(settings, env="production")

    assert settings.DATABASE.host == "db.tenant-a.internal"
    assert settings.DATABASE.port == 5432
    assert settings.DATABASE.pool.size == 20


def test_reserved_variant_raises(settings, ssm):
    settings.set("SSM_PARAMETER_APP_PREFIX_FOR_DYNACONF", "acme")
    settings.set("SSM_PARAMETER_TENANT_FOR_DYNACONF", "tenant-a")
    settings.set("SSM_PARAMETER_TENANT_VARIANT_FOR_DYNACONF", "default")

    with pytest.raises(ValueError, match="TENANT_VARIANT"):
        loader.load(settings, env="production")


def test_single_key_prefers_variant_then_tenant_then_app(settings, put_params):
    put_params(
        {
            "/acme/production/app/default/webhook_secret": "app-level",
            "/acme/production/tenants/tenant-a/default/webhook_secret": "tenant-level",
            "/acme/production/tenants/tenant-a/v2/webhook_secret": "variant-level",
        }
    )
    settings.set("SSM_PARAMETER_APP_PREFIX_FOR_DYNACONF", "acme")
    settings.set("SSM_PARAMETER_TENANT_FOR_DYNACONF", "tenant-a")
    settings.set("SSM_PARAMETER_TENANT_VARIANT_FOR_DYNACONF", "v2")

    loader.load(settings, env="production", key="webhook_secret")

    assert settings.WEBHOOK_SECRET == "variant-level"


def test_single_key_falls_back_past_missing_variant_tier(settings, put_params):
    put_params(
        {
            "/acme/production/app/default/webhook_secret": "app-level",
            "/acme/production/tenants/tenant-a/default/webhook_secret": "tenant-level",
        }
    )
    settings.set("SSM_PARAMETER_APP_PREFIX_FOR_DYNACONF", "acme")
    settings.set("SSM_PARAMETER_TENANT_FOR_DYNACONF", "tenant-a")
    settings.set("SSM_PARAMETER_TENANT_VARIANT_FOR_DYNACONF", "v2")

    loader.load(settings, env="production", key="webhook_secret")

    assert settings.WEBHOOK_SECRET == "tenant-level"


def test_lists_accumulate_across_tiers(settings, put_params):
    """Documented consequence of Dynaconf's deep merge."""
    put_params(
        {
            "/acme/production/app/default/allowed_hosts": '@json ["a.example.com"]',
            "/acme/production/tenants/tenant-a/default/allowed_hosts": (
                '@json ["b.example.com"]'
            ),
        }
    )
    settings.set("SSM_PARAMETER_APP_PREFIX_FOR_DYNACONF", "acme")
    settings.set("SSM_PARAMETER_TENANT_FOR_DYNACONF", "tenant-a")

    loader.load(settings, env="production")

    assert settings.ALLOWED_HOSTS == ["a.example.com", "b.example.com"]


def test_access_denied_on_one_tier_is_soft_even_when_not_silent(
    settings, put_params, monkeypatch
):
    put_params({"/acme/production/tenants/tenant-a/default/api_key": "tenant-key"})
    settings.set("SSM_PARAMETER_APP_PREFIX_FOR_DYNACONF", "acme")
    settings.set("SSM_PARAMETER_TENANT_FOR_DYNACONF", "tenant-a")

    real_fetch = loader._fetch_all_parameters

    def fake_fetch(client, base_path, silent=True):
        if base_path == "/acme/production/app/default":
            raise_denied = ClientError(
                {"Error": {"Code": "AccessDeniedException", "Message": "nope"}},
                "GetParametersByPath",
            )
            loader._handle_client_error(raise_denied, base_path, silent)
            return None
        return real_fetch(client, base_path, silent=silent)

    monkeypatch.setattr(loader, "_fetch_all_parameters", fake_fetch)

    loader.load(settings, env="production", silent=False)

    assert settings.API_KEY == "tenant-key"


def test_genuine_client_error_still_raises_when_not_silent(settings, ssm, monkeypatch):
    settings.set("SSM_PARAMETER_APP_PREFIX_FOR_DYNACONF", "acme")

    def boom(client, base_path, silent=True):
        exc = ClientError(
            {"Error": {"Code": "ThrottlingException", "Message": "slow down"}},
            "GetParametersByPath",
        )
        loader._handle_client_error(exc, base_path, silent)

    monkeypatch.setattr(loader, "_fetch_all_parameters", boom)

    with pytest.raises(ClientError):
        loader.load(settings, env="production", silent=False)


def test_variant_whitespace_is_stripped_before_building_paths(settings, put_params):
    put_params({"/acme/production/tenants/tenant-a/v2/api_key": "variant-key"})
    settings.set("SSM_PARAMETER_APP_PREFIX_FOR_DYNACONF", "acme")
    settings.set("SSM_PARAMETER_TENANT_FOR_DYNACONF", "tenant-a")
    settings.set("SSM_PARAMETER_TENANT_VARIANT_FOR_DYNACONF", "  v2  ")

    loader.load(settings, env="production")

    assert settings.API_KEY == "variant-key"
    assert settings.SSM_PARAMETER_TENANT_VARIANT_FOR_DYNACONF == "v2"


def test_tenant_and_prefix_whitespace_is_stripped(settings, put_params):
    put_params({"/acme/production/tenants/tenant-a/default/api_key": "tenant-key"})
    settings.set("SSM_PARAMETER_APP_PREFIX_FOR_DYNACONF", " acme ")
    settings.set("SSM_PARAMETER_TENANT_FOR_DYNACONF", " tenant-a ")

    loader.load(settings, env="production")

    assert settings.API_KEY == "tenant-key"
    assert settings.SSM_PARAMETER_APP_PREFIX_FOR_DYNACONF == "acme"
    assert settings.SSM_PARAMETER_TENANT_FOR_DYNACONF == "tenant-a"


def test_surrounding_slashes_on_prefix_are_tolerated(settings, put_params):
    put_params({"/acme/production/app/default/api_key": "app-key"})
    settings.set("SSM_PARAMETER_APP_PREFIX_FOR_DYNACONF", "/acme/")

    loader.load(settings, env="production")

    assert settings.API_KEY == "app-key"


def test_malformed_tenant_raises(settings, ssm):
    settings.set("SSM_PARAMETER_APP_PREFIX_FOR_DYNACONF", "acme")
    settings.set("SSM_PARAMETER_TENANT_FOR_DYNACONF", "tenant a")

    with pytest.raises(ValueError, match="SSM_PARAMETER_TENANT_FOR_DYNACONF"):
        loader.load(settings, env="production")

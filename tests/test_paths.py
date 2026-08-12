import pytest

from dynaconf_ssm_tenant_loader.loader import VARIANT_KEY, build_paths, validate_variant
from dynaconf_ssm_tenant_loader.util import normalize_path_segment


def test_app_only():
    assert build_paths("acme", "production") == ["/acme/app/production"]


def test_tenant_without_variant():
    assert build_paths("acme", "production", "tenant-a") == [
        "/acme/app/production",
        "/acme/tenants/tenant-a/production",
    ]


def test_variant_adds_tiers_without_replacing_them():
    assert build_paths("acme", "production", "tenant-a", "v2") == [
        "/acme/app/production",
        "/acme/app/v2/production",
        "/acme/tenants/tenant-a/production",
        "/acme/tenants/tenant-a/v2/production",
    ]


def test_validate_variant_assumes_normalized_input():
    """Whitespace stripping is the caller's job; this is a shape-blind check."""
    validate_variant("v2", "production")  # no raise


@pytest.mark.parametrize(
    "variant",
    ["production", "PRODUCTION", "staging", "default", "global", "app", "tenants"],
)
def test_reserved_variants_rejected(variant):
    with pytest.raises(ValueError, match="TENANT_VARIANT"):
        validate_variant(variant, "production")


def test_ordinary_variant_accepted():
    validate_variant("v2", "production")


def test_variant_case_is_preserved_in_paths():
    assert build_paths("acme", "production", "tenant-a", "V2") == [
        "/acme/app/production",
        "/acme/app/V2/production",
        "/acme/tenants/tenant-a/production",
        "/acme/tenants/tenant-a/V2/production",
    ]


@pytest.mark.parametrize("variant", ["", "   ", "v2/extra"])
def test_malformed_variants_rejected_during_normalization(variant):
    """
    Shape is enforced by `normalize_path_segment`, which `load` applies
    before `validate_variant` ever sees the value.
    """
    with pytest.raises(ValueError, match=VARIANT_KEY):
        normalize_path_segment(VARIANT_KEY, variant)

import pytest

from dynaconf_ssm_tenant_loader.loader import VARIANT_KEY, build_paths, validate_variant
from dynaconf_ssm_tenant_loader.util import normalize_path_segment


def test_app_only():
    assert build_paths("acme", "production") == ["/acme/production/app/default"]


def test_tenant_without_variant():
    assert build_paths("acme", "production", "tenant-a") == [
        "/acme/production/app/default",
        "/acme/production/tenants/tenant-a/default",
    ]


def test_variant_adds_tiers_without_replacing_them():
    assert build_paths("acme", "production", "tenant-a", "v2") == [
        "/acme/production/app/default",
        "/acme/production/app/v2",
        "/acme/production/tenants/tenant-a/default",
        "/acme/production/tenants/tenant-a/v2",
    ]


def test_no_tier_is_a_path_prefix_of_another():
    """
    The ``default`` leaf exists precisely so that a recursive read of
    one tier can never swallow a sibling variant tier.
    """
    paths = build_paths("acme", "production", "tenant-a", "v2")
    for path in paths:
        for other in paths:
            if path != other:
                assert not other.startswith(path + "/")


@pytest.mark.parametrize(
    "variant",
    ["default", "DEFAULT", "app", "tenants", "Tenants"],
)
def test_reserved_variants_rejected(variant):
    with pytest.raises(ValueError, match="TENANT_VARIANT"):
        validate_variant(variant)


@pytest.mark.parametrize(
    "variant",
    ["v2", "pr-1234", "production", "staging", "global"],
)
def test_non_structural_variants_accepted(variant):
    """
    Env names are no longer reserved: with the environment ahead of
    the variant slot, they cannot make a path ambiguous.
    """
    validate_variant(variant)  # no raise


def test_variant_case_is_preserved_in_paths():
    assert build_paths("acme", "production", "tenant-a", "V2") == [
        "/acme/production/app/default",
        "/acme/production/app/V2",
        "/acme/production/tenants/tenant-a/default",
        "/acme/production/tenants/tenant-a/V2",
    ]


@pytest.mark.parametrize("variant", ["", "   ", "v2/extra"])
def test_malformed_variants_rejected_during_normalization(variant):
    """
    Shape is enforced by `normalize_path_segment`, which `load` applies
    before `validate_variant` ever sees the value.
    """
    with pytest.raises(ValueError, match=VARIANT_KEY):
        normalize_path_segment(VARIANT_KEY, variant)

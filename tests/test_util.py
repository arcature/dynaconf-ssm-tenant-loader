import pytest

from dynaconf_ssm_tenant_loader.util import normalize_path_segment


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  v2  ", "v2"),
        ("\tv2\n", "v2"),
        ("V2", "V2"),
        ("tenant-a", "tenant-a"),
    ],
)
def test_surrounding_whitespace_is_stripped_and_case_preserved(raw, expected):
    assert normalize_path_segment("KEY", raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "\t"])
def test_empty_values_rejected(raw):
    with pytest.raises(ValueError, match="empty or whitespace"):
        normalize_path_segment("KEY", raw)


def test_internal_whitespace_rejected():
    with pytest.raises(ValueError, match="whitespace"):
        normalize_path_segment("KEY", "tenant a")


def test_slashes_rejected_by_default():
    with pytest.raises(ValueError, match="single path segment"):
        normalize_path_segment("KEY", "a/b")


def test_slashes_allowed_and_trimmed_when_permitted():
    assert normalize_path_segment("KEY", " /acme/team-b/ ", allow_slashes=True) == (
        "acme/team-b"
    )


def test_empty_inner_segment_rejected():
    with pytest.raises(ValueError, match="empty path segment"):
        normalize_path_segment("KEY", "acme//team-b", allow_slashes=True)

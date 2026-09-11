import pytest

from app.endpoints import (
    CONTENT_PLUGINS,
    global_api,
    plugin_api,
    validate_name,
    validate_plugin,
)


def test_plugin_api_builds_domain_scoped_path():
    assert plugin_api("default", "repositories/rpm/rpm/") == (
        "/pulp/default/api/v3/repositories/rpm/rpm/"
    )


def test_plugin_api_rejects_bad_domain():
    for bad in ["", "Default", "a b", "../admin", "x" * 64, "-leading", "sl/ash"]:
        with pytest.raises(ValueError):
            plugin_api(bad, "status/")


def test_global_api_builds_flat_path():
    assert global_api("groups/1/roles/") == "/pulp/api/v3/groups/1/roles/"


def test_content_plugins_are_enumerated():
    assert set(CONTENT_PLUGINS) == {"rpm", "deb", "python", "ansible", "container"}


def test_validate_name_accepts_pulp_safe_names():
    assert validate_name("domain", "dummy-alpha") == "dummy-alpha"
    assert validate_name("domain", "a_b-1") == "a_b-1"


def test_validate_name_rejects_traversal_and_whitespace():
    for bad in ["", "a/b", "..", "a b", "A", "a" * 64, "-a", "a."]:
        with pytest.raises(ValueError):
            validate_name("domain", bad)


def test_validate_plugin_rejects_unknown():
    assert validate_plugin("rpm") == "rpm"
    with pytest.raises(ValueError):
        validate_plugin("../../etc/passwd")


def test_validate_name_rejects_non_str():
    for bad in [123, None, ["a"], {"a": 1}, b"a"]:
        with pytest.raises(ValueError):
            validate_name("domain", bad)


def test_validate_plugin_rejects_non_str():
    for bad in [123, None, ["rpm"], {"plugin": "rpm"}]:
        with pytest.raises(ValueError):
            validate_plugin(bad)

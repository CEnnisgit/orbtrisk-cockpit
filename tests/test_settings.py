from app.settings import settings


def _snapshot_settings() -> dict:
    keys = (
        "trusted_hosts",
        "trusted_hosts_allow_all",
        "allowed_origins",
        "render_external_hostname",
    )
    return {key: getattr(settings, key) for key in keys}


def _restore_settings(snapshot: dict) -> None:
    for key, value in snapshot.items():
        setattr(settings, key, value)


def test_trusted_hosts_normalizes_scheme_path_and_port():
    snapshot = _snapshot_settings()
    try:
        settings.trusted_hosts_allow_all = False
        settings.trusted_hosts = "https://orbitrisk.net,http://www.orbitrisk.net:443/path,orbitrisk.onrender.com"
        settings.allowed_origins = None
        settings.render_external_hostname = None

        hosts = settings.trusted_hosts_list
        assert "orbitrisk.net" in hosts
        assert "www.orbitrisk.net" in hosts
        assert "orbitrisk.onrender.com" in hosts
    finally:
        _restore_settings(snapshot)


def test_trusted_hosts_includes_allowed_origins_and_render_hostname():
    snapshot = _snapshot_settings()
    try:
        settings.trusted_hosts_allow_all = False
        settings.trusted_hosts = "localhost"
        settings.allowed_origins = "https://orbitrisk.net,https://www.orbitrisk.net/path"
        settings.render_external_hostname = "orbitrisk.onrender.com"

        hosts = settings.trusted_hosts_list
        assert "orbitrisk.net" in hosts
        assert "www.orbitrisk.net" in hosts
        assert "orbitrisk.onrender.com" in hosts
    finally:
        _restore_settings(snapshot)


def test_trusted_hosts_allow_all_short_circuit():
    snapshot = _snapshot_settings()
    try:
        settings.trusted_hosts_allow_all = True
        settings.trusted_hosts = "orbitrisk.net"
        settings.allowed_origins = "https://www.orbitrisk.net"
        settings.render_external_hostname = "orbitrisk.onrender.com"

        assert settings.trusted_hosts_list == ["*"]
    finally:
        _restore_settings(snapshot)


def test_render_fallback_prevents_host_lockout_when_unconfigured():
    snapshot = _snapshot_settings()
    try:
        settings.trusted_hosts_allow_all = False
        settings.trusted_hosts = "localhost,127.0.0.1,testserver"
        settings.allowed_origins = None
        settings.render_external_hostname = "orbitrisk.onrender.com"

        assert settings.trusted_hosts_list == ["*"]
    finally:
        _restore_settings(snapshot)

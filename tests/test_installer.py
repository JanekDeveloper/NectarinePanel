"""Installer validation tests."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_shell_scripts_parse() -> None:
    """Ensure every installer shell entrypoint has valid Bash syntax."""
    scripts = sorted((ROOT / "installer").glob("*.sh"))
    assert scripts
    subprocess.run(  # noqa: S603 - fixed Bash executable and repository scripts
        ["/usr/bin/bash", "-n", *map(str, scripts)],
        check=True,
    )


def test_installer_dry_run() -> None:
    """Validate installer assets without mutating the host."""
    subprocess.run(  # noqa: S603 - fixed local installer with inert dry-run
        [
            str(ROOT / "installer" / "install.sh"),
            "--dry-run",
            "--non-interactive",
            "--domain",
            "panel.example.com",
            "--email",
            "owner@example.com",
            "--admin-password",
            "test-password-for-installer",
            "--backend-port",
            "18080",
            "--frontend-port",
            "13000",
            "--agent-port",
            "18090",
            "--adminer-port",
            "18081",
            "--skip-ssl",
        ],
        check=True,
    )


def test_installer_templates_support_custom_internal_ports() -> None:
    """Production installer can avoid loopback port collisions on existing hosts."""
    install_script = (ROOT / "installer" / "install.sh").read_text(encoding="utf-8")
    update_script = (ROOT / "installer" / "update.sh").read_text(encoding="utf-8")
    panel_env = (ROOT / "installer/templates/panel.env").read_text(encoding="utf-8")
    nginx = (ROOT / "installer/nginx/nectarine-panel.conf").read_text(encoding="utf-8")
    backend_unit = (ROOT / "installer/systemd/vps-panel-backend.service").read_text(
        encoding="utf-8"
    )
    frontend_unit = (ROOT / "installer/systemd/vps-panel-frontend.service").read_text(
        encoding="utf-8"
    )
    agent_unit = (ROOT / "installer/systemd/vps-panel-agent.service").read_text(
        encoding="utf-8"
    )
    assert "--backend-port" in install_script
    assert "ADMINER_PORT" in update_script
    assert "AGENT_URL=http://127.0.0.1:@AGENT_PORT@" in panel_env
    assert "proxy_pass http://127.0.0.1:@BACKEND_PORT@;" in nginx
    assert "--port @BACKEND_PORT@" in backend_unit
    assert "Environment=PORT=@FRONTEND_PORT@" in frontend_unit
    assert "--port @AGENT_PORT@" in agent_unit


def test_update_and_uninstall_dry_runs() -> None:
    """Updater and uninstaller expose inert validation paths for CI."""
    for script in ("update.sh", "uninstall.sh"):
        subprocess.run(  # noqa: S603 - fixed local scripts with inert dry-run
            [str(ROOT / "installer" / script), "--dry-run"],
            check=True,
        )


def test_installer_keeps_telegram_bot_service_running() -> None:
    """Bot service waits for database configuration instead of being disabled."""
    install_script = (ROOT / "installer" / "install.sh").read_text(encoding="utf-8")
    update_script = (ROOT / "installer" / "update.sh").read_text(encoding="utf-8")
    bot_unit = (ROOT / "installer/systemd/vps-panel-telegram-bot.service").read_text(
        encoding="utf-8"
    )

    assert "vps-panel-telegram-bot.service" in install_script
    assert "systemctl disable --now vps-panel-telegram-bot.service" not in install_script
    assert 'systemctl enable "${SERVICES[@]}"' in update_script
    assert 'systemctl start "${SERVICES[@]}"' in update_script
    assert "Restart=always" in bot_unit


def test_installer_pins_external_runtime_artifacts() -> None:
    """Production installation avoids mutable PM2 and Adminer release tags."""
    install_script = (ROOT / "installer" / "install.sh").read_text(encoding="utf-8")
    update_script = (ROOT / "installer" / "update.sh").read_text(encoding="utf-8")
    for content in (install_script, update_script):
        assert 'ADMINER_IMAGE="adminer:5.4.2-standalone"' in content
        assert 'PM2_VERSION="7.0.1"' in content
        assert 'npm install --global --omit=dev "pm2@$PM2_VERSION"' in content


def test_installer_does_not_print_provided_admin_password() -> None:
    """Production logs must not expose externally supplied owner credentials."""
    install_script = (ROOT / "installer" / "install.sh").read_text(encoding="utf-8")
    assert "Admin password: provided externally; not printed." in install_script
    assert 'log "Admin password: $ADMIN_PASSWORD"' in install_script
    assert 'if [[ "$ADMIN_PASSWORD_GENERATED" == true ]]; then' in install_script


def test_installer_uses_mariadb_client_package() -> None:
    """Installation avoids default MySQL client conflicts on MariaDB hosts."""
    install_script = (ROOT / "installer" / "install.sh").read_text(encoding="utf-8")
    assert "mariadb-client" in install_script
    assert "default-mysql-client" not in install_script


def test_installer_keeps_existing_package_conffiles() -> None:
    """Package installation must not block on production conffile prompts."""
    install_script = (ROOT / "installer" / "install.sh").read_text(encoding="utf-8")
    assert "Dpkg::Options::=--force-confdef" in install_script
    assert "Dpkg::Options::=--force-confold" in install_script


def test_installer_retries_docker_startup() -> None:
    """Docker package replacement can transiently fail before socket settles."""
    install_script = (ROOT / "installer" / "install.sh").read_text(encoding="utf-8")
    assert "systemctl reset-failed docker" in install_script
    assert 'docker info >/dev/null 2>&1 || fail "Docker daemon is not available"' in (
        install_script
    )


def test_installer_runs_panel_commands_with_non_root_home() -> None:
    """Migration and admin commands must not inherit root PostgreSQL client paths."""
    install_script = (ROOT / "installer" / "install.sh").read_text(encoding="utf-8")
    assert 'HOME="$INSTALL_DIR"' in install_script
    assert 'PYTHONPATH="$INSTALL_DIR/backend:$INSTALL_DIR/worker' in install_script
    assert 'run_panel_command "$INSTALL_DIR/.venv/bin/alembic" upgrade head' in (install_script)


def test_agent_runs_non_root_through_exact_helper() -> None:
    """Production agent HTTP service delegates only to a root-owned sudo helper."""
    unit = (ROOT / "installer/systemd/vps-panel-agent.service").read_text(encoding="utf-8")
    sudoers = (ROOT / "installer/sudoers/nectarine-agent").read_text(encoding="utf-8")
    helper = (ROOT / "installer/helpers/nectarine-agent-helper").read_text(encoding="utf-8")
    assert "User=@AGENT_USER@" in unit
    assert "User=root" not in unit
    assert "NoNewPrivileges=true" not in unit
    assert '/usr/local/libexec/nectarine-agent-helper ""' in sudoers
    assert "/usr/local/libexec/nectarine-agent-helper --terminal" in sudoers
    assert "system_agent.privileged_helper" in helper
    assert '"$@"' in helper


def test_uninstaller_stops_managed_project_runtimes() -> None:
    """Removing the panel also revokes project services, containers, and SFTP users."""
    content = (ROOT / "installer" / "uninstall.sh").read_text(encoding="utf-8")
    assert "/etc/systemd/system/vps-project-*.service" in content
    assert "label=io.nectarine.project" in content
    assert "com.docker.compose.project" in content
    assert '[[ "$username" == vps_* ]]' in content


def test_dev_compose_agent_joins_docker_socket_group() -> None:
    """Local non-root agent receives the Docker socket group explicitly."""
    content = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "/var/run/docker.sock:/var/run/docker.sock" in content
    assert "DOCKER_GID:-998" in content


def test_nginx_rate_limits_login_and_sets_security_headers() -> None:
    """Protect the public login route and browser responses at the edge."""
    content = (ROOT / "installer/nginx/nectarine-panel.conf").read_text(encoding="utf-8")

    assert "limit_req_zone $binary_remote_addr zone=nectarine_login:10m" in content
    assert "location = /api/v1/auth/login" in content
    assert "limit_req zone=nectarine_login burst=5 nodelay" in content
    assert 'add_header Strict-Transport-Security "max-age=31536000" always;' in content
    assert 'add_header Cross-Origin-Opener-Policy "same-origin" always;' in content

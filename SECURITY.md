# Security Policy

## Supported versions

NectarinePanel is pre-1.0 software. Security fixes are provided for the latest
tagged release and the `main` branch only.

## Reporting a vulnerability

Use the repository's private **Security advisories → Report a vulnerability**
flow. Do not open a public issue and do not include production credentials,
database dumps, environment files, private keys, or customer data.

Include the affected version, deployment mode, impact, reproduction steps, and
a minimal proof of concept. You should receive an acknowledgement within seven
days. Disclosure timing is coordinated after a fix is available.

## Deployment responsibility

The panel controls privileged VPS resources. Install only tagged releases,
review installer changes before running them as root, keep the agent and
internal services bound to localhost/private networks, and maintain off-host
backups. See [docs/en/security.md](docs/en/security.md) for the trust model and
hardening requirements.

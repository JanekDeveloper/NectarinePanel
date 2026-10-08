# Dependency audit

Run `npm run audit` (or `make audit-frontend` from the repository root).
CI uses the same command. It runs the registry's `npm audit --json` and fails
on every high or critical advisory except the two explicitly approved entries
in `audit-exceptions.json`. Registry errors and invalid responses also fail.

Nuxt DevTools 3.4.2 imports the removed default export of `simple-git`.
A scoped override selects the fixed `simple-git` 4.0.2; `patch-package` switches
DevTools to its named `simpleGit` export during `npm ci`. Installation fails if
the patch cannot be applied. Remove the override and patch when upstream
DevTools adopts a fixed compatible dependency.

The temporary exceptions expire at **2026-11-07 00:00 UTC**. They cover only:

- [braces: recursive pattern processing](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm),
  inherited through build-time glob matching on repository-controlled patterns;
- [node-forge: RSA signature verification](https://github.com/advisories/GHSA-86w9-cpqp-85rv),
  inherited through development TLS tooling. Production TLS uses Nginx;
  NectarinePanel does not use forge for signature verification.

Neither advisory has a published upstream fix as of 2026-10-08. These are
accepted risks, not remediated vulnerabilities. Do not expose development TLS
or feed untrusted request patterns into build tooling.

Remove each exception when its dependency is fixed or removed. Expired entries
fail CI even if the corresponding finding has disappeared. Do not extend dates
or add advisory exceptions without review. New advisories in the same packages
remain blocking. Plain `npm audit --audit-level=high` still reports the accepted
findings; the wrapper prints each exception and its expiration date explicitly.

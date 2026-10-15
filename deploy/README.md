# Production deployment

Target: `ubuntu@98.88.244.85`, domain `getpulse.testappcd.com`.

GitHub Actions tests SQLite and PostgreSQL, builds one image for `main`, uploads
it as a short-lived artifact and transfers it over SSH with host-key verification.
The server loads the exact commit image and runs Compose with health checks.
If startup fails, the release script tries to restore the previous image.
This does not roll back database changes; future schema migrations need backups
and a separate migration strategy.

## GitHub Secrets

In repository Settings → Secrets and variables → Actions, add:

| Secret | Value |
| --- | --- |
| `DEPLOY_SSH_KEY` | Entire dedicated OpenSSH private key, including BEGIN/END lines |
| `DEPLOY_KNOWN_HOSTS` | Verified `98.88.244.85 ssh-ed25519 ...` host-key line |

They may also be configured as secrets of the `production` environment.
Local files prepared for these secrets are `secrets/gamepulse_deploy` and
`secrets/deploy_known_hosts`. The entire directory is excluded from Git and Docker.
Do not use unverified `ssh-keyscan` output in the deployment workflow.

Runtime credentials live only in `/opt/gamepulse/.env` (mode 600), outside the
repository and image. The CI job does not need OpenAI/YouTube keys or login passwords.

## Server layout and initial setup

- `/opt/gamepulse/releases/<commit>`: release Compose file and script.
- `/opt/gamepulse/current`: symlink to the last successful release.
- Compose project: `gamepulse`; persistent PostgreSQL and media volumes.
- Web port: `127.0.0.1:18000`; Nginx proxies only the dedicated domain.
- Existing services on port 8000 and other virtual hosts remain separate.
- SSH user requires passwordless sudo for Docker. Docker access implies root access;
  restrict the dedicated key to this repository and rotate it if compromised.

After creating an A record pointing the domain to `98.88.244.85`, enable HTTPS:

```bash
sudo certbot --nginx -d getpulse.testappcd.com --redirect
```

Port 80 and 443 must be allowed in the server firewall/security group.
The production configuration uses `COOKIE_SECURE=true`; log in through HTTPS.

Commit and push to `main` to deploy, or use Actions → quality → Run workflow
on the `main` branch. Pull requests run checks without access to deployment keys.

On the server, inspect status without printing secrets:

```bash
cd /opt/gamepulse/current
export GAMEPULSE_IMAGE="gamepulse:$(basename "$(pwd -P)")"
sudo --preserve-env=GAMEPULSE_IMAGE docker compose -p gamepulse --env-file /opt/gamepulse/.env -f compose.production.yaml ps
curl -f http://127.0.0.1:18000/health
```

The production database starts empty; the worker populates it after deployment.
The local SQLite database is not copied. Keep PostgreSQL backups; Docker volumes
survive redeployments but do not replace backups. Old images are retained for
rollback; monitor disk space and remove obsolete releases deliberately.

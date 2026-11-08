# systemd timers for backups

Nightly off-site backup (`tg-bot-backup.timer`) and monthly restore drill
(`tg-bot-drill.timer`) for the VDS.

## Prerequisites

- Repo at `/srv/tg-bot-eng-teacher`, user `deploy`, `.env` (mode 600).
- `rclone` configured for the R2 remote (`rclone config` as `deploy`).
- `.env` contains `BACKUP_REMOTE`; for encrypted backups also
  `BACKUP_AGE_RECIPIENT` and `AGE_IDENTITY`.
- Verify the remote before enabling the timer:

  ```bash
  sudo -u deploy env BACKUP_REMOTE=r2:<bucket>/backups \
    /srv/tg-bot-eng-teacher/scripts/setup_r2.sh --init
  sudo -u deploy env BACKUP_REMOTE=r2:<bucket>/backups \
    /srv/tg-bot-eng-teacher/scripts/backup_r2.sh --dry-run
  ```

## Install

```bash
sudo cp deploy/systemd/tg-bot-backup.service deploy/systemd/tg-bot-backup.timer \
  deploy/systemd/tg-bot-drill.service deploy/systemd/tg-bot-drill.timer \
  /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tg-bot-backup.timer tg-bot-drill.timer
systemctl list-timers 'tg-bot-*'
```

## First run and checks

```bash
sudo systemctl start tg-bot-backup.service
sudo journalctl -u tg-bot-backup.service -n 80 --no-pager
sudo systemctl start tg-bot-drill.service
sudo journalctl -u tg-bot-drill.service -n 80 --no-pager
```

The backup service runs `backup_r2.sh --prune-remote`. Add
`--verify-download` after setting `AGE_IDENTITY` to prove the uploaded object
can be downloaded and decrypted.

## Failure alerts

Set `BACKUP_PING_URL` to a healthchecks.io check. The backup script pings it on
success and `.../fail` on any failure, so a missed or failed nightly run
triggers the external alert configured for that check.

## Notes

- Timers run on the host, not inside the bot container.
- `tg-bot-drill.service` is drill-only; it never replaces the live database.
- R2 lifecycle rules are the recommended remote rotation; `--prune-remote` is a
  fallback.

# Off-server backup (Backblaze B2): setup, restore test, disaster recovery

**Why:** the nightly backup (`/opt/techdeva-backup.sh`, 02:15) works, but it writes to `/opt/backups/daily` on the same server. If the server is lost, every backup goes with it. This adds one **encrypted** copy per night on Backblaze B2.

**What goes off-site every night** (`ops/backup/offsite.sh`, 02:40 UTC):
- the newest nightly set as it is: platform database (every client), clinic CRM database, settings (`.env` files, Traefik);
- every WhatsApp agent's memory (all `hermes-*` volumes, the clinic's Zara and workspace included), copied **read-only** by a throwaway container (half a CPU, no network). No agent is stopped, restarted or changed;
- every agent's folder (`/docker/hermes-*`), the post videos, and root's crontab.

**How it is protected:**
| Risk | Protection |
|---|---|
| Someone reads the copy in B2 | Encrypted on the server with **age** before upload. Only your private key opens it |
| The server is hacked | The server holds only the **public key**: it can lock new backups, never unlock any. The private key is never on the server, in `.env`, in B2 or in GitHub |
| A hacker or a mistake deletes the copies | B2 **Object Lock**: every copy is locked for 30 days, even against the server's own B2 key |
| The upload is silently broken | The uploaded file is checked against its checksum; any failure makes the job fail and is written to `/var/lib/techdeva/offsite-last.json` (alerts come in step B) |
| The local backup failed | Nothing is uploaded unless the nightly backup reported `failures=0` |

Never paste the B2 keys or the private key into a chat, a document or GitHub. Type them only where these steps say.

---

## One-time setup (about 20 minutes)

### Step 1 — Backblaze (in the browser)
1. Create a Backblaze account at backblaze.com and choose **B2 Cloud Storage**.
2. **Buckets → Create a Bucket**:
   - name: `techdeva-backups-` plus a few random letters (names are global, so `techdeva-backups` is probably taken). Write the name down;
   - Files in bucket: **Private**;
   - **Object Lock: Enable**.
3. Open the bucket's **Object Lock** setting and set a **default retention of 30 days** (mode **Governance** is enough; **Compliance** means not even you can delete early).
4. Open the bucket's **Lifecycle Settings → custom rule**: file name prefix `daily/`, hide after **31** days, delete **1** day after hiding. This keeps about 30 days of copies.
5. **Application Keys → Add a New Application Key**: name `techdeva-server`, access **only this bucket**, type **Read and Write**. Backblaze shows a **keyID** and an **applicationKey** once. Keep that page open for step 3.

### Step 2 — the key pair (on the server)
The private key is created in memory (`/dev/shm`), shown to you once, and deleted. Only the public key stays on the server.
```
apt install -y age rclone
mkdir -p /etc/techdeva && age-keygen -o /dev/shm/td-key.txt
cat /dev/shm/td-key.txt
```
Save the whole output (the line starting `AGE-SECRET-KEY-1` is what matters) in **two places that are not this server**, for example your password manager and a printed copy in a safe place. **If this key is lost, the off-site copies cannot be opened by anyone.**
```
age-keygen -y /dev/shm/td-key.txt > /etc/techdeva/backup-recipient.txt && shred -u /dev/shm/td-key.txt
cat /etc/techdeva/backup-recipient.txt     # the public key: one line starting age1, safe to show
```

### Step 3 — connect the server to the bucket
```
rclone config
```
Answer: `n` (new remote) → name **`techdeva-b2`** → storage type **`b2`** → `account`: paste the **keyID** → `key`: paste the **applicationKey** → `hard_delete`: press Enter (false) → advanced config: `n` → `y` to keep → `q` to quit.

Then tell the scripts the bucket name (not a secret; replace `<bucket>`):
```
echo "OFFSITE_TARGET=techdeva-b2:<bucket>/daily" > /etc/techdeva/offsite.conf
```

### Step 4 — first copy, by hand
```
cd /opt/techdeva-platform/techdeva-platform
bash ops/backup/offsite.sh --dry-run      # lists what it will copy, with sizes; uploads nothing
bash ops/backup/offsite.sh                # makes and uploads tonight's copy
```
The last line must say `done. failures=0`. Paste the output to Claude: it contains no secrets.

### Step 5 — restore test (this is what makes A finished)
```
bash ops/backup/offsite-restore-test.sh
```
It asks for the private key: paste the `AGE-SECRET-KEY-1...` line (nothing appears while you paste) and press Enter. It then:
- downloads the newest copy from Backblaze and checks its checksum;
- decrypts it with your key;
- restores the platform database into a **throwaway** database and counts businesses, users and customers;
- opens the clinic CRM and runs its integrity check;
- checks the settings, every agent folder, every agent memory volume, the videos and the crontab.

Nothing live is changed. The key is held in memory only and deleted when the test ends; the throwaway database and files are deleted, pass or fail. The output ends with `Result: PASSED`. Paste it to Claude.

### Step 6 — schedule it
```
bash ops/backup/install-cron.sh
```
It refuses until the restore test has passed. It adds one line (02:40 UTC) and changes nothing else.

**Then, once a month:** run step 5 again. A backup that is never restored is not a backup.

---

## If the server is lost: recovery outline
1. Get a new Ubuntu 24.04 server; install Docker, `age` and `rclone`; repeat step 3 (`rclone config`) with the same B2 key, or a new one.
2. Download and open the newest copy (you type the private key):
   ```
   rclone lsf techdeva-b2:<bucket>/daily | sort | tail -2
   rclone copy techdeva-b2:<bucket>/daily/<name>.tar.age /root/restore/
   nano /dev/shm/key.txt        # paste the AGE-SECRET-KEY-1... line, save
   age -d -i /dev/shm/key.txt /root/restore/<name>.tar.age | tar xf - -C /root/restore/
   shred -u /dev/shm/key.txt
   ```
3. Put the settings back: `tar xzf nightly/config_*.tgz -C /` (platform `.env`, clinic `.env`, Traefik).
4. Clone the platform code from GitHub into `/opt/techdeva-platform/techdeva-platform`, start the database container, restore `nightly/techdeva_*.dump` with `pg_restore`, then start api, worker and web.
5. Clinic CRM: put `nightly/leads_*.db` back as `/opt/beauty-dashboard/leads.db` (its code is in its own repository).
6. Agents: `tar xzf agent-folders.tgz -C /docker`; for each `volumes/<name>.tgz` create the docker volume and untar into it; then `docker compose up -d` in each `/docker/hermes-*` folder. WhatsApp numbers may need scanning again.
7. Point DNS (`api`, `*`, `portal`, `crm`) to the new server's address; Traefik gets new HTTPS certificates by itself.
8. Put the crontab back from `crontab.txt`.

Known limit: the databases are consistent snapshots. Agent memory is copied while the agents run, so a chat being written at 02:40 may lose its last message.

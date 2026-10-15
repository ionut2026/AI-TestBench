# Site deployment config: an EXAMPLE. Copy it OUTSIDE this repository (for example to the Jenkins agent's home
# or D:\rig\deploy.toml), replace every <placeholder>, then point to the copy with --config or GRF_DEPLOY_CONFIG.
# GRF refuses a config inside the repository for passwords, keys and known_hosts, and any key named
# password/token/secret/passphrase. Guide: docs/DEPLOYMENT.md in GeneralRobotFramework.
schema = 1

[jenkins]
base_url = "https://<jenkins-host>/jenkins"
# ca_bundle = "<path to the company CA bundle (PEM), outside the repository>"
# The Jenkins user and API token come from these environment variables (Jenkins withCredentials sets them):
user_env = "JENKINS_USER"
token_env = "JENKINS_TOKEN"

# One table per board. The password is never written here: name the environment variable that holds it
# (password_env, set by Jenkins withCredentials) or a file outside the repository (password_file),
# or use a private key (identity).
[targets.board-1]
host = "<board IP or host name>"
user = "<board user>"
password_env = "BOARD_1_PASSWORD"
known_hosts = "<path outside the repository, e.g. D:/rig/known_hosts>"
host_key_policy = "accept-new"   # or "pinned" once the board's key is in known_hosts
workdir = "/home/root"           # where GRF keeps tarballs, previous_builds, its lock and markers

# Which component goes to which board (names from `grf deploy components`).
[components]
# <component> = "board-1"

[limits]
min_free_mb = 1500   # preflight: free disk space on the board's workdir
max_load = 5.0       # preflight: 1-minute load average
retention = 2        # earlier builds kept on the board for rollback
lock_ttl_s = 7200    # a deployment lock older than this may be broken with --break-stale-lock

# audit_log = "<path outside the repository>/deployments.jsonl"   # one JSON line per deployment

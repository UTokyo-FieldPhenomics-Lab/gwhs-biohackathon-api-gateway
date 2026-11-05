# The Nano Banana backend, on the gateway host

One container beside the gateway, listening on `127.0.0.1:8010`. It serves the three hosted ids —
`nano-banana-2`, `nano-banana-2.1`, `nano-banana-pro` — and the compute is Google's: the process
holds no weights and no card, and it authenticates to Vertex AI with a mounted service account. The
gateway reaches it like any other backend; the difference is that the machine is the host it already
runs on, and the thing being spent is money rather than GPU time.

The constraints this procedure carries out are in
[`../.agents/rules/models.md`](../.agents/rules/models.md) (it is a model service, and its
implementation is private) and [`../.agents/rules/deploy.md`](../.agents/rules/deploy.md) (host
state, credentials, and the container that is not the gateway).

## What it needs

- Docker Engine on the host. The **gateway stays systemd**; Docker exists for this one model
  container, exactly as it does on a GPU host.
- The image, built from `models/nanobanana` in the private submodule at a recorded commit.
- A GCP service account in `gen-lang-client-0341367599` with `roles/aiplatform.user` (or a custom
  role holding `aiplatform.endpoints.predict`), and a JSON key for it.
- Two host directories: one for the configuration and the key, one for the ledger.

## 1. Docker on the host, once

```sh
sudo apt-get update
sudo apt-get install -y docker.io
sudo systemctl enable --now docker
```

The gateway itself stays on systemd. Docker exists on this host for the one model container, just as
it does on a GPU host; no other container is added here.

## 2. Build the image on the workstation, load it on the host

The host is public. It holds the image, not the private source, and no registry credential.

```sh
cd models/nanobanana
docker build -t nanobanana-backend:"$(git -C ../.. rev-parse --short HEAD)" .
docker save nanobanana-backend:<tag> | gzip \
  | ssh gwhs-host 'gunzip | sudo docker load'

# Record the submodule commit and the image id together: that pair is the identity a rebuild must
# reproduce.
ssh gwhs-host 'sudo docker image inspect --format "{{.Id}}" nanobanana-backend:<tag>'
```

## 3. The host's directories and secret

```sh
sudo install -d -m 700 /etc/biohack-nanobanana
sudo install -d -m 750 -o 10001 -g 10001 /var/lib/biohack-nanobanana

# The service-account key: readable by the container's uid, not by the login user (except sudo).
sudo install -m 400 -o 10001 -g 10001 sa.json /etc/biohack-nanobanana/sa.json

# The environment, 0600 root: Docker reads it as root and passes the values into the container.
sudo install -m 600 /dev/null /etc/biohack-nanobanana/env
sudo tee /etc/biohack-nanobanana/env >/dev/null <<'EOF'
BIOHACK_NANOBANANA_BACKEND_TOKEN=the-same-value-as-the-gateway-s-BIOHACK_BACKEND_TOKEN
BIOHACK_NANOBANANA_TEAM_CREDITS=200
BIOHACK_NANOBANANA_BALANCE_JPY=47159
BIOHACK_NANOBANANA_BALANCE_FLOOR_JPY=5000
BIOHACK_NANOBANANA_DAILY_JPY=5000
BIOHACK_NANOBANANA_USD_JPY=150
BIOHACK_NANOBANANA_MAX_IN_FLIGHT=2
EOF
```

The key is a **file mount, never an `ENV` value and never an image layer**: an environment variable
is visible in `docker inspect`, and a `COPY` is visible in the history of every pulled image.

## 4. The run line

```sh
sudo docker run -d --name nanobanana --restart unless-stopped \
  -p 127.0.0.1:8010:8010 \
  --env-file /etc/biohack-nanobanana/env \
  --mount type=bind,src=/etc/biohack-nanobanana/sa.json,dst=/run/secrets/gcp-sa,readonly \
  --mount type=bind,src=/var/lib/biohack-nanobanana,dst=/state \
  --user 10001:10001 --read-only --tmpfs /tmp \
  --cap-drop ALL --security-opt no-new-privileges \
  <image-id>
```

- The port is published on **loopback only**. No security-group rule, no `data.*` path, and no
  CloudFront behaviour reaches it.
- `/state` is the only writable path, and it holds the ledger.
- `--read-only` and `--tmpfs /tmp` keep Pillow's scratch out of the image.

## 5. Register the three ids on the gateway

`~/.config/biohack-gateway/models.json` is host state, not a repository file. Add one entry per id,
each naming the prefix the container serves:

```json
{
  "nano-banana-2": ["http://127.0.0.1:8010/nano-banana-2"],
  "nano-banana-2.1": ["http://127.0.0.1:8010/nano-banana-2.1"],
  "nano-banana-pro": ["http://127.0.0.1:8010/nano-banana-pro"]
}
```

Then restart the gateway so it reads the declarations, and reload Caddy for the `/generate` body
cap:

```sh
sudo systemctl restart biohack-gateway
sudo systemctl reload caddy
```

The gateway's declaration is read once per process; a changed parameter set or a changed response
budget needs this restart, not a request.

## 6. Verify

```sh
curl -s http://127.0.0.1:8010/nano-banana-2.1/health          # the declaration, no token
curl -s -H "X-API-Key: $KEY" https://api.global-wheat.com/models
```

The directory must show three rows with `backends_up: 1`, and each row's parameters must list
`prompt`, `aspect_ratio` and `image_size`. Then one generation:

```sh
curl -s -H "X-API-Key: $KEY" \
  "https://api.global-wheat.com/models/nano-banana-2.1/generate?prompt=a%20wheat%20field&image_size=1K" \
  -o /tmp/picture.json
```

The body carries `objects: []`, one `image` whose `data` decodes to a PNG, and a line in
`/var/lib/biohack-nanobanana/ledger.jsonl`. A call with no key answers `401`.

An **edit** of a picture whose ratio is not one of the ten carries `frame` as well: the file's own
size and the box it occupies in `image`, as fractions. Upload a 2:1 crop to `/predict/upload` and the
frame's width and height are the file's, `content` is not `[0, 0, 1, 1]`, and the picture's height is
the canvas's. Naming `aspect_ratio` instead answers without `frame` and with a `reframed` warning.

## 7. The money

| variable | what it does |
|---|---|
| `BIOHACK_NANOBANANA_TEAM_CREDITS` | lifetime allowance per team; 1K 2.1 = 1, 2K 2.1 = 1.5, NB2 1K = 2, Pro 1K = 4 |
| `BIOHACK_NANOBANANA_BALANCE_JPY` | the pot's starting figure |
| `BIOHACK_NANOBANANA_BALANCE_FLOOR_JPY` | the reserve; at it, every team is refused |
| `BIOHACK_NANOBANANA_DAILY_JPY` | the daily circuit breaker, reset at Japan midnight |
| `BIOHACK_NANOBANANA_USD_JPY` | the conversion the ledger records |

Topping up means raising `BALANCE_JPY` by what was added and restarting. Lowering the per-team
allowance is the same operation. The ledger is the only record of what was spent and by which team:
copy it off the host with the participant token file, because a lost ledger overstates the balance
and resets every team's allowance.

## 8. Rotate and revoke

- **Rotate**: create a key in the console, `install -m 400 -o 10001 -g 10001 new.json
  /etc/biohack-nanobanana/sa.json`, `sudo docker restart nanobanana`, confirm one generation, then
  delete the old key. The process reads the file on the first paid call; a rotation is a restart.
- **Revoke**: delete the key, or disable the service account. The container then answers `502`; stop
  it with `sudo docker stop nanobanana` if the ids should leave the directory.
- The account holds only `roles/aiplatform.user` (or the custom predict role): it cannot change IAM,
  delete resources, or open machines. A key that leaks costs Vertex calls, which is why the ledger
  and the balance floor exist.

## 9. When something is wrong

| what you see | where to look |
|---|---|
| `/models` lists the ids with `backends_up: 0` | `sudo docker logs nanobanana`; the container is not running or `/health` is not answering |
| `/health` is 200, a generation is `502` | the service-account key, its role, or the Vertex API; the container log names the upstream status |
| `429` with "image credits" | the team's lifetime allowance; the message names the number |
| `429` with "daily image budget" | the circuit breaker; it resets at Japan midnight |
| `429` with "shared image budget" | the pot is at its floor; top up `BALANCE_JPY` or stop the ids |
| `429` with "pictures are being made" | the process's `MAX_IN_FLIGHT`; retry, or raise it after measuring memory |
| `504` | the upstream did not answer inside the declared budget; check Google's status, then the model |
| `413` | the edge's 15 MB upload cap; the picture is resized before it is sent, not after |
| the host is short of memory | `sudo docker stats --no-stream`; the container is about 150 MB and the host has about 1.4 GB free |

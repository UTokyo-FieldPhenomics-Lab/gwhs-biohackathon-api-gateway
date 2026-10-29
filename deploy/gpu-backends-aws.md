# The backend you rent

How an EC2 instance in the gateway's own VPC becomes a backend, and why the machine itself is not the
thing to keep. **An on-prem machine stays and has to survive a reboot —
[`gpu-backends-local.md`](gpu-backends-local.md).** A rented one is billed by the hour, so its
lifecycle is *launch, serve, terminate*, and what outlives it is a thin base image plus an image
digest. Which machines exist, and who may reach them, is in
[`../.agents/rules/backend-pool.md`](../.agents/rules/backend-pool.md).

## 1. The path

The machine sits in the gateway's own VPC, so the host reaches its private address directly: no
tailnet, no router advertising a subnet, no forward.

Its security group admits `22` and the model ports from the gateway host's **group** and nothing
else. An address allowlist would not work here — Docker's proxy and the NAT in front of it rewrite the
visible source.

The private address is named at launch, inside the gateway host's own subnet, so the `models.json`
line is written once and never edited. EC2 refuses a launch whose address is already taken rather
than binding another: a failed command, not a silently wrong backend.

A public IPv4 is attached for egress only — there is no NAT gateway, and the machine must reach ECR
and `apt` before it has anything to run. A default subnet assigns one even when the private address is
named; a subnet that does not needs an Elastic IP rather than a wider inbound rule. Inbound stays closed. The fixed address pins one AZ, because
a subnet is one AZ.

The machine bills by the hour, so its undo is `terminate-instances`, not `docker stop`.

## 2. Three things you prepare once

Five names are AWS's, and each is an ordinary thing: an **AMI** is a frozen disk image that a launch
copies from; a **launch template** is a saved launch form; **ECR** is a private Docker registry;
**SSM** is a parameter store; an **instance profile** is the role a machine assumes.

| Prepared once | What it is | Here |
|---|---|---|
| Credential | an access key the CLI uses, with write rights on EC2, ECR, IAM and SSM | §3 |
| Base AMI | Debian + NVIDIA driver + Docker, about 5 GB | §5 |
| Image digest | the model container, published to ECR | §6 |

Around those three sit a handful of AWS objects that are created once and never edited between runs:
the security group, the key pair, the instance role, the token in SSM, and the launch template
(§4, §7).

A launch is then one command. The template carries the type, subnet, group, fixed address and disk;
its user-data logs in to ECR, pulls the digest and starts the container. Nothing about the machine
survives a terminate.

## 3. The CLI and its credential

**Install AWS-CLI**

Install where the commands will be typed, and keep `~/.aws/` out of this checkout.

Manjaro/Arch — the AUR package of the official v2 bundle:

```bash
yay -S aws-cli-v2
```

Debian/Ubuntu — the official bundle:

```bash
curl -s "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip
cd /tmp && unzip -q awscliv2.zip && sudo ./aws/install
```

macOS:

```bash
brew install awscli
```

**Two ways to be credentialed; the second is the one to keep.**

- `aws login` — a browser sign-in as the console identity. Quick, and it **expires**: commands then
  answer `NoCredentials`. It is how you bootstrap the user below, and how you get in on a machine that
  has no key.
- A long-lived access key belonging to a dedicated user — created once, then written to
  `~/.aws/credentials`.

**A user for this, not the publisher's.** `biohack-publish` writes dataset prefixes and should keep
only that; a GPU operator needs EC2, ECR, IAM and SSM. Create the user with the console identity —
`aws login` is enough for these commands:

```bash
aws iam create-user --user-name biohack-gpu-operator
aws iam attach-user-policy --user-name biohack-gpu-operator \
  --policy-arn arn:aws:iam::aws:policy/AmazonEC2FullAccess
aws iam attach-user-policy --user-name biohack-gpu-operator \
  --policy-arn arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryFullAccess
```

**Then the scoped policy.** Save this as `biohack-gpu-operator.json` beside the shell you are in — a
browser CloudShell has no checkout, so create the file there first — and attach it:

```json
{"Version": "2012-10-17", "Statement": [
  {"Effect": "Allow", "Action": ["ssm:GetParameter", "ssm:PutParameter"],
   "Resource": "arn:aws:ssm:eu-central-1:<account>:parameter/biohack-gateway/*"},
  {"Effect": "Allow", "Action": "ssm:DescribeParameters", "Resource": "*"},
  {"Effect": "Allow",
   "Action": ["iam:CreateRole", "iam:GetRole", "iam:AttachRolePolicy", "iam:PutRolePolicy",
              "iam:CreateInstanceProfile", "iam:AddRoleToInstanceProfile",
              "iam:GetInstanceProfile", "iam:PassRole"],
   "Resource": ["arn:aws:iam::<account>:role/biohack-*",
                "arn:aws:iam::<account>:instance-profile/biohack-*"]}]}
```

```bash
aws iam put-user-policy --user-name biohack-gpu-operator \
  --policy-name biohack-gpu-scoped --policy-document file://biohack-gpu-operator.json
```

`ssm:DescribeParameters` is the one action that will not take the parameter path as a resource — it is
account-wide by nature, and scoping it to `/biohack-gateway/*` answers `AccessDenied`. **IAM is the
part that must be scoped**: `iam:*` on `*` would let the operator mint itself any role it liked, which
is administrator by another name, so the wildcard is on `biohack-*` names. EC2 and ECR are the managed
policies because a machine created the wrong way needs the same permission as one created right.

**Then the key, which AWS shows exactly once.** This is still the console identity: the operator cannot
create or read its own keys — that is the scoped IAM above working as intended — so a shell already
configured with the operator's key answers `AccessDenied` here. In the console: IAM → Users →
`biohack-gpu-operator` → *Security credentials* → *Create access key*.

```bash
aws iam create-access-key --user-name biohack-gpu-operator
```

**Then hand it to the CLI:**

```bash
aws configure
```

That asks for the key ID, the secret, `eu-central-1` and an output format, and writes
`~/.aws/credentials` (0600). `aws configure list` shows whether a key or a login session is in force.
From here the CLI **is** the operator: it can build the backend, and any IAM change needs the console
identity again.

Then two checks, neither of which costs anything:

```bash
aws sts get-caller-identity
aws ec2 describe-key-pairs --key-names <the backend key pair>   # must exist, or ssh cannot work
```

**The region is `eu-central-1`, never the zone `eu-central-1c`**: a zone there builds a hostname that
does not exist, and every command then fails as though the network were broken. The identity also
needs write rights on EC2, ECR, IAM and SSM, not read rights — a read-only key passes `sts` and fails
at the first `run-instances`.

## 4. One-time account resources

**The security group.** Its two rules name the gateway host's group, which the host itself will tell
you — this is why the file holds no id of its own:

```bash
# on the gateway host: its own VPC, subnet, zone and group
TOKEN=$(curl -s -X PUT http://169.254.169.254/latest/api/token \
  -H 'X-aws-ec2-metadata-token-ttl-seconds: 60')
MAC=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" \
  http://169.254.169.254/latest/meta-data/network/interfaces/macs/)
for key in vpc-id subnet-id security-group-ids; do
  curl -s -H "X-aws-ec2-metadata-token: $TOKEN" \
    "http://169.254.169.254/latest/meta-data/network/interfaces/macs/${MAC}${key}"; echo
done
```

```bash
aws ec2 create-security-group --group-name biohack-gpu-backend \
  --description "GPU backend: 22 and the model ports, from the gateway host group only" \
  --vpc-id <the host's vpc> --query GroupId --output text

aws ec2 authorize-security-group-ingress --group-id <the new group> --ip-permissions \
  "IpProtocol=tcp,FromPort=22,ToPort=22,UserIdGroupPairs=[{GroupId=<the host's group>}]" \
  "IpProtocol=tcp,FromPort=12711,ToPort=12713,UserIdGroupPairs=[{GroupId=<the host's group>}]"
```

**The key pair.** One of its own rather than the host's, because a key pair on the account and the
private key on a workstation are two different things and the host's may not be available:

```bash
aws ec2 create-key-pair --key-name biohack-gpu-backend --key-type rsa --key-format pem \
  --query KeyMaterial --output text > ~/.ssh/biohack-gpu-backend.pem
chmod 600 ~/.ssh/biohack-gpu-backend.pem
```

The material is printed exactly once; a lost private key means a new key pair and a line in
`~/.ssh/config`:

```
Host aws-euc1-l4
  HostName <the fixed private address>
  User admin
  IdentityFile ~/.ssh/biohack-gpu-backend.pem
  ProxyJump gwhs-host
```

`ProxyJump` is what keeps `22` closed to the internet: the connection arrives from the gateway host,
the only source the group admits.

**The instance role**, so the machine can pull its image and read its token without a credential on
disk:

```bash
aws iam create-role --role-name biohack-gpu-backend \
  --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow",
    "Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}'

aws iam attach-role-policy --role-name biohack-gpu-backend \
  --policy-arn arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly

aws iam put-role-policy --role-name biohack-gpu-backend --policy-name read-backend-token \
  --policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow",
    "Action":"ssm:GetParameter","Resource":"arn:aws:ssm:<region>:<account>:parameter/biohack-gateway/backend-token"}]}'

aws iam create-instance-profile --instance-profile-name biohack-gpu-backend
aws iam add-role-to-instance-profile --instance-profile-name biohack-gpu-backend \
  --role-name biohack-gpu-backend
```

**The token, in SSM.** One SecureString, written once and edited only when the gateway's
`BIOHACK_BACKEND_TOKEN` is rotated:

```bash
aws ssm put-parameter --name /biohack-gateway/backend-token --type SecureString \
  --value "$(grep '^BIOHACK_BACKEND_TOKEN=' <the host's env> | cut -d= -f2-)" --overwrite
```

It is in user-data's absence that this matters: user-data is readable from the instance metadata
service by anything running on the box, and it is displayed in the console.

## 5. The base AMI

**What it is:** Debian 13 with the NVIDIA driver, Docker, the container toolkit and the AWS CLI. No
model, no weights, no token. About 4 GB used. It exists because the driver is the one step that fails —
on Debian a DKMS module compiled against the running kernel — and would otherwise be paid on every
launch.

Build it from a stock Debian AMI, and read the **root device name** it reports (`/dev/xvda` here):
§7 carries it verbatim.

```bash
aws ec2 describe-images --owners 136693071363 \
  --filters 'Name=name,Values=debian-13-amd64-*' 'Name=state,Values=available' \
  --query 'reverse(sort_by(Images,&CreationDate))[0].[ImageId,Name,RootDeviceName]' --output text
```

Launch it with 20 GB, then run this as `user-data` (or paste it over ssh, the result is the same):

```bash
#!/bin/bash
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive

# `nvidia-driver` lives in non-free. Debian's cloud images list their components in the deb822
# `debian.sources` file, not in the old one-line sources.list.
sed -i 's/^Components: main$/Components: main contrib non-free non-free-firmware/' \
  /etc/apt/sources.list.d/debian.sources

apt-get update
apt-get install -y --no-install-recommends \
  "linux-headers-$(uname -r)" ca-certificates curl gnupg

# The driver first: everything below can be reinstalled in a minute, this cannot.
# DKMS compiles the module against the kernel that is running — five to ten minutes on four vCPU.
apt-get install -y nvidia-driver

# Docker, from Docker's own archive rather than the distribution's.
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin awscli

# The toolkit, from NVIDIA's archive.
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  > /etc/apt/sources.list.d/nvidia-container-toolkit.list
apt-get update
apt-get install -y nvidia-container-toolkit

nvidia-ctk runtime configure --runtime=docker
systemctl enable --now docker

# Freeze the kernel with the image. A DKMS module and the kernel it was compiled against are one
# fact: an unattended kernel upgrade would spend the next boot compiling before the card appears.
apt-mark hold linux-image-cloud-amd64 "linux-image-$(uname -r)" "linux-headers-$(uname -r)"

reboot
```

Then prove three things before freezing it — a base image that fails any of them is not a base image:

```bash
nvidia-smi
dkms status                                   # nvidia/<version>, <kernel>: installed
docker run --rm --gpus all nvidia/cuda:12.8.1-runtime-ubuntu24.04 nvidia-smi -L
```

```bash
aws ec2 create-image --instance-id <the temporary instance> --name base-gpu-<date> \
  --description "Debian 13 + NVIDIA driver (DKMS) + docker + toolkit; no model"
aws ec2 wait image-available --image-ids <the new AMI>
aws ec2 terminate-instances --instance-ids <the temporary instance>
```

**Ubuntu instead of Debian** — owner, image name, root device, SSH user and the driver package that
differs — is recorded in
[`../.agents/references/20261027_aws-gpu-backend.md`](../.agents/references/20261027_aws-gpu-backend.md).

**Update it only when the driver or Docker must change.** A model update is a push to ECR; a new base
AMI means a new launch-template version (§7), and the old one stays as the rollback.

## 6. The model image, in ECR

The container's source and build are the same as on-prem —
[`gpu-backends-local.md`](gpu-backends-local.md) §3.1, the 2.1 MB archive from `models/`. What
changes is where the result goes.

```bash
aws ecr create-repository --repository-name sam3.1-meta \
  --image-tag-mutability IMMUTABLE --image-scanning-configuration scanOnPush=true
```

On a machine that has the built image (the first time, the one that built it):

```bash
REGISTRY=<account>.dkr.ecr.<region>.amazonaws.com
aws ecr get-login-password --region <region> \
  | docker login --username AWS --password-stdin "$REGISTRY"
docker tag sam3.1-meta "$REGISTRY/sam3.1-meta:20261027"
docker push "$REGISTRY/sam3.1-meta:20261027"
docker image inspect --format '{{index .RepoDigests 0}}' "$REGISTRY/sam3.1-meta:20261027"
```

The first push moves about 10 GB (24.2 GB of layers, compressed); later pushes send only the layers
that changed, which is 3.5 GB when the checkpoint changes and a few hundred kilobytes when only code
does. **The digest is the identity** — a tag is a convenience for humans, and §7 pins the digest.
`IMMUTABLE` stops a tag from being silently repointed at a different image.

Why this rather than a disk with the image on it: a registry pull is free in-region, needs no mount,
survives an AZ failing, and gives every answer a name that a rollback can address. A 60 GB EBS volume
of Docker state costs more per idle month than the image in ECR and has to be attached.

## 7. The launch template and its user-data

The template is the machine's shape, and the only thing that changes between bakes is the AMI id or
the digest. `NetworkInterfaces` carries the subnet, the group and the fixed address together — you
cannot pass `--security-group-ids` beside it.

```bash
aws ec2 create-launch-template --launch-template-name biohack-gpu-backend \
  --launch-template-data '{
    "ImageId": "<the base AMI>",
    "InstanceType": "g6.xlarge",
    "KeyName": "biohack-gpu-backend",
    "IamInstanceProfile": {"Name": "biohack-gpu-backend"},
    "NetworkInterfaces": [{
      "DeviceIndex": 0,
      "SubnetId": "<the host's subnet>",
      "Groups": ["<the backend group>"],
      "PrivateIpAddress": "<the fixed address>"
    }],
    "BlockDeviceMappings": [{
      "DeviceName": "<the AMI's root device: /dev/xvda on Debian, /dev/sda1 on Ubuntu>",
      "Ebs": {"VolumeSize": 100, "VolumeType": "gp3"}
    }],
    "TagSpecifications": [{
      "ResourceType": "instance",
      "Tags": [{"Key": "Name", "Value": "aws-euc1-l4"}]
    }]
  }'
```

`g6.xlarge` is one L4 and 24 GB — an eighth of the account's 32 vCPU, and every measured model fits in
a third of the card. 100 GB is the root volume: the system, plus two model images at 24 and 18 GB
unpacked. Both are deleted with the instance.

The user-data does the three things a launch needs and nothing else. It is the same for every launch,
so it can live in the template, and it starts **every model the machine serves** — one container per
model, each on its own host port:

```bash
#!/bin/bash
set -euxo pipefail
REGION=<region>
REGISTRY=<account>.dkr.ecr."$REGION".amazonaws.com
SAM31=sha256:<digest from §6>
GWFSS=sha256:<the other model's digest>

aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "$REGISTRY"

TOKEN=$(aws ssm get-parameter --name /biohack-gateway/backend-token \
  --with-decryption --query Parameter.Value --output text)

docker pull "$REGISTRY/sam3.1-meta@$SAM31"
docker run -d --gpus all --restart unless-stopped -p 12711:12711 \
  -e BACKEND_TOKEN="$TOKEN" -e SAM31_MACHINE=aws-euc1-l4 \
  --name sam3.1-meta "$REGISTRY/sam3.1-meta@$SAM31"

docker pull "$REGISTRY/gwfss-segformer-b4@$GWFSS"
docker run -d --gpus all --restart unless-stopped -p 12712:12711 \
  -e BACKEND_TOKEN="$TOKEN" -e GWFSS_MACHINE=aws-euc1-l4 \
  --name gwfss-segformer-b4 "$REGISTRY/gwfss-segformer-b4@$GWFSS"
```

`-p 12712:12711` is the shape to copy: the model's port on the host, the container's own `12711`
inside, because the two containers never share a network namespace.

The pull is one to two minutes at in-region speed; the container then prints `budget ok` and
`ready: load … s` and starts answering. `--restart unless-stopped` is what makes the container come
back if the instance reboots, and it is not a substitute for the user-data: a *terminated* machine has
no user-data to re-run, which is why every returning machine is a new launch.

## 8. Verify, then list it

**Prove it from the gateway host, without logging in.** That is the whole claim of this file, and it is
the only test that covers boot, role, pull, token and group at once:

```bash
# on the gateway host
for i in $(seq 1 40); do
  code=$(curl -s -o /dev/null -w '%{http_code}' http://<the fixed address>:12711/health)
  echo "$(date -u +%H:%M:%S) $code"
  [ "$code" = 200 ] && break
  sleep 10
done
```

`503` early is the checkpoint loading and is the right thing to see. Nothing after seven minutes is a
failure of one of the four links — role, registry, token, group — and `/var/log/cloud-init-output.log`
on the instance says which.

Then the token probe and a real prediction, both described in
[`gpu-backends-local.md`](gpu-backends-local.md) §4: put the address first in `models.json`, restart
the gateway, send one `(dataset, row)` request through it, and read `computed_by.machine`.

**Change the query text for each new machine.** The gateway caches a successful answer by
`(dataset, revision, row, model, query)`, and a hit keeps the *original* `computed_by` — so a repeat
of an earlier question answers in milliseconds, naming the machine that produced it last time. A
prediction that returns in 5 ms did not reach the new backend at all. Vary one parameter, or the row,
and read the elapsed time as well as the machine.

**`ssh-keygen -R <the fixed address>` after every relaunch.** The address is the same and the machine
is not, so the host key changes and `ssh` refuses with a man-in-the-middle warning that is really just
a new instance. Remove the stale entry once, then connect.

**Remove the line and terminate the instance when you are done.** The address is stable, so the line
that returns later is the same line — but an entry whose instance no longer exists is a connect
timeout paid every thirty seconds, on a machine nobody intends to start again.

## 9. What it costs, and what to delete

| Idle | Size | $/month |
|---|---|---|
| Base AMI snapshot | ~10 GB | 0.5 |
| ECR, one model (compressed layers) | ~10 GB | 1.0 |
| **Total** | | **~1.5** |
| For comparison: whole-machine AMI | 104 GB snapshot | 5.2 |
| For comparison: a stopped instance | 120 GB gp3 | 11.4 |

A launch costs about $0.13 for ten minutes of `g6.xlarge`. Nothing else bills while no instance runs.

**Delete what you are not using.** A superseded base AMI is a snapshot nobody launches; an old digest
in ECR is $0.10/GB-month for a rollback nobody will need, so a lifecycle policy that keeps three
images is the right shape. An Elastic IP exists only because a subnet without auto-assign needs egress;
release it with the machine. The security group, the key pair, the role and the template are free and
are what make the next launch one command.

**The fallback, if the layered design fails:** a whole-machine AMI of one served instance — the
container, the weights and the driver in one snapshot, launched from the same template with no role
and no pull. It costs about 3.5 times as much per idle month and hides the digest, so it is a
temporary retreat, not a design.

## 10. What fails, and what it means

| Symptom | Cause | Where to look |
|---|---|---|
| `InsufficientInstanceCapacity` on launch | the AZ is out of that type, not a quota | retry in minutes; another AZ costs the fixed address. Every size in every AZ can be dry at once for hours — then a new template version with a different type is the only move |
| `VcpuLimitExceeded` on launch | the size needs more than the account's G/VT quota | the 8xlarge sizes need 32 vCPU, which is the whole limit; stay at 4xlarge or below |
| launch fails, "IP address in use" | the fixed address is taken | the subnet's other instances; pick another and edit `models.json` |
| `cloud-init` stops at `docker login` | the instance profile is not attached, or lacks ECR read | `aws sts get-caller-identity` on the box |
| `docker pull` 403 | the digest is gone, or the repo policy changed | `aws ecr describe-images` |
| `/health` 401 | SSM token ≠ the host's `BIOHACK_BACKEND_TOKEN` | §4 of the local file |
| `/health` stays 503 | the checkpoint is loading, or the card cannot take it | `docker logs`, `budget ok` line |
| health never answers, `22` does | the group admits `22` but not `12711` | the group's ingress, not the container |

## 11. Where the rest is written down

| What | Where |
|---|---|
| The same contract, on a machine that stays | [`gpu-backends-local.md`](gpu-backends-local.md) |
| Which machines exist and who may reach them | [`../.agents/rules/backend-pool.md`](../.agents/rules/backend-pool.md) |
| Why the compute plane is shaped this way | [`../.agents/plans/2026101609_gpu-compute-plane.md`](../.agents/plans/2026101609_gpu-compute-plane.md) |
| What the rented path measured, and what broke | [`../.agents/references/20261027_aws-gpu-backend.md`](../.agents/references/20261027_aws-gpu-backend.md) |
| The containers and the weights they fetch | the submodule's `README` (private) |

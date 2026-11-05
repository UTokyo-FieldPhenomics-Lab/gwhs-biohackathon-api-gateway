#!/usr/bin/env bash
# What runs *on* a captured g7e: prove the card, pull the images, start them, time one call each.
#
# Rendered by deploy/probe-gpu-capacity.sh with @REGISTRY@ and @TAG@ substituted, then passed as
# user-data, so nothing has to reach the instance over SSH and no security-group rule has to move.
# The whole report goes to the serial console, which `ec2 get-console-output` reads back; the probe
# terminates the instance once the closing marker appears, or at its time cap.
#
# It is deliberately honest about what it cannot do: it pulls what ECR holds and times those models.
# A model whose image lives only on an on-prem build (sprout, the two yolo ids) is listed as not
# tried, rather than silently missing.

set -uo pipefail
exec > >(tee /dev/console) 2>&1

echo "=== gpu trial begin ==="
date -Is || true

REGION=eu-central-1
REGISTRY=@REGISTRY@
TAG=@TAG@
TOKEN=""

echo "--- the card ---"
if ! nvidia-smi --query-gpu=name,memory.total,driver_version,compute_cap --format=csv,noheader; then
  echo "VERDICT driver-fails-on-this-card"
  echo "=== gpu trial end ==="
  exit 0
fi

echo "--- docker ---"
docker version --format 'server {{.Server.Version}}' || { echo "VERDICT no-docker"; echo "=== gpu trial end ==="; exit 0; }
nvidia-smi >/dev/null 2>&1 || true

echo "--- registry ---"
if ! aws ecr get-login-password --region "$REGION" \
    | docker login --username AWS --password-stdin "$REGISTRY" >/dev/null 2>&1; then
  echo "VERDICT ecr-login-failed"
  echo "=== gpu trial end ==="
  exit 0
fi
echo "login ok"

TOKEN=$(aws ssm get-parameter --region "$REGION" \
  --name /biohack-gateway/backend-token --with-decryption \
  --query Parameter.Value --output text 2>/dev/null || true)
if [ -z "$TOKEN" ]; then
  echo "VERDICT no-backend-token"
  echo "=== gpu trial end ==="
  exit 0
fi
echo "token ok"

# image:model:port for the six ids ECR carries.
IMAGES=(
  "sam3.1-meta:sam3.1-meta:12711:20261105"
  "gwfss-segformer-b4:gwfss-segformer-b4:12712:20261105"
  "tasselnetv4:tasselnetv4:12713:20261105"
  "dinov3-vitl16:dinov3-vitl16:12714:20261105"
  "dinov3-vith16plus:dinov3-vith16plus:12715:20261105"
  "internvl-u:internvl-u:12716:20261103"
)

echo "--- pull ---"
for entry in "${IMAGES[@]}"; do
  image=${entry%%:*}
  rest=${entry#*:}
  model=${rest%%:*}
  rest=${rest#*:}
  tag=${rest##*:}
  ref="$REGISTRY/$image:$tag"
  start=$(date +%s)
  if docker pull "$ref" >/dev/null 2>&1; then
    echo "pull $model ok $(( $(date +%s) - start ))s"
  else
    echo "pull $model FAILED"
  fi
done

echo "--- run ---"
for entry in "${IMAGES[@]}"; do
  image=${entry%%:*}
  rest=${entry#*:}
  model=${rest%%:*}
  rest=${rest#*:}
  port=${rest%%:*}
  tag=${rest##*:}
  docker rm -f "$model" >/dev/null 2>&1
  case "$model" in
    sam3.1-meta)       envvar="SAM31_MACHINE=aws-g7e-trial" ;;
    gwfss-segformer-b4) envvar="GWFSS_MACHINE=aws-g7e-trial" ;;
    tasselnetv4)       envvar="TNV4_MACHINE=aws-g7e-trial" ;;
    dinov3-vitl16)     envvar="DINOV3_MACHINE=aws-g7e-trial" ;;
    dinov3-vith16plus) envvar="DINOV3_MACHINE=aws-g7e-trial" ;;
    internvl-u)        envvar="INTERNVLU_MACHINE=aws-g7e-trial" ;;
  esac
  docker run -d --gpus all --restart no -p "$port":12711 \
    -e BACKEND_TOKEN="$TOKEN" -e "$envvar" \
    --name "$model" "$REGISTRY/$image:$tag" >/dev/null 2>&1 || echo "run $model FAILED"
done

echo "--- warm ---"
for entry in "${IMAGES[@]}"; do
  rest=${entry#*:}
  model=${rest%%:*}
  port=${rest##*:}
  for _ in $(seq 1 30); do
    code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$port/health" || true)
    [ "$code" = "200" ] && break
    sleep 2
  done
  echo "health $model $code"
done

echo "--- one call each ---"
IMG=https://data.global-wheat.com/gwhd/images/00000000.jpg
BODY="{\"input\":{\"url\":\"$IMG\"}}"

time_call() {
  local model=$1 port=$2 query=$3
  local out
  out=$(curl -s -o /tmp/answer.json -w '%{http_code} %{time_total} %{size_download}' \
    -X POST "http://127.0.0.1:$port/models/$model/predict$query" \
    -H 'Content-Type: application/json' -H "X-Backend-Token: $TOKEN" \
    -d "$BODY" || echo "000 0 0")
  echo "call $model $out"
}

time_call sam3.1-meta       12711 ""
time_call gwfss-segformer-b4 12712 ""
time_call tasselnetv4       12713 "?boxes=%5B%5B420%2C420%2C60%2C60%2C1%5D%5D"
time_call dinov3-vitl16     12714 ""
time_call dinov3-vith16plus 12715 ""
time_call internvl-u        12716 "?prompt=Describe%20this%20image.&mode=text"

echo "--- not tried ---"
echo "sprout-l-gwfss sprout-v2-gwhs yolo26-ultralytics yolo26-seg-ultralytics yolov11-gwhs: image not in ECR"
echo "wevisone-vl-4b: not planned for the rented card"

echo "VERDICT trial-complete"
echo "=== gpu trial end ==="

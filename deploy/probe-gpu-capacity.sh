#!/usr/bin/env bash
# Probe whether Frankfurt will give this account a modern GPU right now.
#
# The account's quota is not the obstacle — 32 vCPU of G/VT covers a g7e.2xlarge — and every refusal
# seen so far is `InsufficientInstanceCapacity`, which is a per-AZ, per-type, per-hour condition. This
# script asks the question once per type and AZ and records the answer, because the answer at 03:00 CET
# is what decides whether the event's rented card can be one of these.
#
# **Any instance that launches is terminated at once.** This is a probe, not a reservation: capacity
# that exists now is not capacity that exists in December, and holding a g7e.2xlarge overnight costs
# about $5.72 an hour. When the event is close enough to hold one, that is a deliberate action, not
# this script's side effect.
#
# The resource identifiers are host-private state and are not in this file: subnet ids, the AMI and the
# security group name a deployment in one account. They live in `~/.config/gwhs/gpu-probe.env`
# (mode 0600), which this script sources when it exists:
#
#     AMI=ami-…
#     SECURITY_GROUP=sg-…
#     PROFILE=biohack-gpu-backend
#     SUBNETS="subnet-…:eu-central-1a subnet-…:eu-central-1b subnet-…:eu-central-1c"
#
# Usage:
#
#     deploy/probe-gpu-capacity.sh              # one pass, appends to the log
#     LOG=/tmp/probe.log deploy/probe-gpu-capacity.sh
#
# Exit status is 0 whether or not capacity was found; read the log, or the last "LAUNCHED" line.

set -uo pipefail

ENV_FILE=${GWH_PROBE_ENV:-$HOME/.config/gwhs/gpu-probe.env}
if [[ -f $ENV_FILE ]]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
fi

: "${AMI:?set AMI in $ENV_FILE}"
: "${SECURITY_GROUP:?set SECURITY_GROUP in $ENV_FILE}"
: "${SUBNETS:?set SUBNETS as space-separated subnet:az pairs in $ENV_FILE}"
REGION=${REGION:-eu-central-1}
PROFILE=${PROFILE:-biohack-gpu-backend}
LOG=${LOG:-$HOME/.local/state/gwhs/gpu-capacity.log}

TYPES=(g7e.2xlarge g7e.4xlarge g6.xlarge g6e.xlarge)

mkdir -p "$(dirname "$LOG")"
exec 9>"$LOG.lock"
if ! flock -n 9; then
  echo "another probe is running" >&2
  exit 0
fi

for type in "${TYPES[@]}"; do
  for pair in $SUBNETS; do
    subnet=${pair%%:*}
    az=${pair##*:}

    out=$(aws ec2 run-instances --region "$REGION" \
      --image-id "$AMI" --instance-type "$type" --subnet-id "$subnet" \
      --security-group-ids "$SECURITY_GROUP" --iam-instance-profile "Name=$PROFILE" \
      --count 1 \
      --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=capacity-probe},{Key=purpose,Value=quota-and-capacity-check}]" \
      --query 'Instances[0].InstanceId' --output text 2>&1)

    case "$out" in
      i-*)
        echo "$(date -Is) $type $az LAUNCHED $out — terminating" >>"$LOG"
        aws ec2 terminate-instances --region "$REGION" --instance-ids "$out" \
          --query 'TerminatingInstances[0].CurrentState.Name' --output text >>"$LOG" 2>&1
        ;;
      *InsufficientInstanceCapacity*)
        echo "$(date -Is) $type $az no-capacity" >>"$LOG"
        ;;
      *"not supported in your requested Availability Zone"*)
        echo "$(date -Is) $type $az unsupported-in-az" >>"$LOG"
        ;;
      *)
        echo "$(date -Is) $type $az other: $(printf '%s' "$out" | tr '\n' ' ' | cut -c1-200)" >>"$LOG"
        ;;
    esac
  done
done

echo "$(date -Is) pass complete" >>"$LOG"
if [[ -n ${VERBOSE:-} ]]; then
  tail -n 13 "$LOG"
fi

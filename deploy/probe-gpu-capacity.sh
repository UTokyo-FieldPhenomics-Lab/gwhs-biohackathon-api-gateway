#!/usr/bin/env bash
# Probe whether Frankfurt will give this account a modern GPU right now, and take the first g7e.
#
# The account's quota is not the obstacle — 32 vCPU of G/VT covers a g7e.2xlarge — and every refusal
# seen so far is `InsufficientInstanceCapacity`, which is a per-AZ, per-type, per-hour condition. This
# script asks the question once per type and AZ and records the answer, because the answer at 03:00 CET
# is what decides whether the event's rented card can be one of these.
#
# **A short-lived launch is terminated at once.** Probing is not reserving: capacity that exists now is
# not capacity that exists in December.
#
# **The first g7e is different.** It is kept, the trial user-data
# (`deploy/aws-gpu-trial-userdata.sh`, rendered here) is already running on it, and this script reads the
# report off the serial console before terminating. **The card is destroyed after 60 minutes whatever the
# trial reached** — a hard cap, not a best effort: an unattended instance at $5.72/h is a bill, and the
# report is the thing worth keeping. Every later g7e is a probe again.
#
# The resource identifiers are host-private state and are not in this file. They live in
# `~/.config/gwhs/gpu-probe.env` (mode 0600), which this script sources when it exists:
#
#     AMI=ami-…                       # the pool's own base image, for the probes
#     TRIAL_AMI=ami-…                 # a Blackwell-capable image, for the g7e trial
#     SECURITY_GROUP=sg-…
#     PROFILE=biohack-gpu-backend
#     REGISTRY=<account>.dkr.ecr.eu-central-1.amazonaws.com
#     TAG=20261105
#     SUBNETS="subnet-…:eu-central-1a subnet-…:eu-central-1b subnet-…:eu-central-1c"
#
# Usage:
#
#     deploy/probe-gpu-capacity.sh              # one pass
#     PROBE_UNTIL=2026-11-13 deploy/probe-gpu-capacity.sh
#
# Exit status is 0 whether or not capacity was found; read the log.

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
STATE_DIR=${STATE_DIR:-$HOME/.local/state/gwhs}
TRIAL_MARKER="$STATE_DIR/g7e-trial-done"
TRIAL_MAX_MINUTES=${TRIAL_MAX_MINUTES:-60}
PROBE_UNTIL=${PROBE_UNTIL:-2026-11-13}
TRIAL=${TRIAL:-1}
TYPES=(g7e.2xlarge g7e.4xlarge g6.xlarge g6e.xlarge)

mkdir -p "$STATE_DIR"
exec 9>"$LOG.lock"
if ! flock -n 9; then
  echo "another probe is running" >&2
  exit 0
fi

if [[ $(date +%Y-%m-%d) > $PROBE_UNTIL ]]; then
  echo "$(date -Is) probe window closed at $PROBE_UNTIL — removing the crontab entry" >>"$LOG"
  crontab -l 2>/dev/null | grep -v 'probe-gpu-capacity.sh' | crontab - 2>/dev/null || true
  exit 0
fi

terminate() {
  aws ec2 terminate-instances --region "$REGION" --instance-ids "$1" \
    --query 'TerminatingInstances[0].CurrentState.Name' --output text >>"$LOG" 2>&1 || true
}

# Read the serial console until the trial's closing marker appears or the cap is reached, then stop the
# card. Always terminates: a refused or timed-out trial is a finding, not a reason to keep paying.
run_trial() {
  local id=$1 deadline report
  deadline=$(( $(date +%s) + TRIAL_MAX_MINUTES * 60 ))
  report="$STATE_DIR/g7e-trial-$(date +%Y%m%dT%H%M%S).log"

  while [[ $(date +%s) -lt $deadline ]]; do
    aws ec2 get-console-output --region "$REGION" --instance-id "$id" --latest \
      --query Output --output text >"$report" 2>/dev/null || true
    if grep -q '=== gpu trial end ===' "$report" 2>/dev/null; then
      echo "$(date -Is) trial report in $report" >>"$LOG"
      break
    fi
    sleep 30
  done

  # The last console dump is kept whether or not the trial finished: that file is the experiment record.
  aws ec2 get-console-output --region "$REGION" --instance-id "$id" --latest \
    --query Output --output text >"$report" 2>/dev/null || true
  grep -E 'VERDICT|^call |^health |^pull |^driver|^--- the card' "$report" 2>/dev/null | tail -40 >>"$LOG"
  echo "$(date -Is) trial finished after $(( ( $(date +%s) - (deadline - TRIAL_MAX_MINUTES * 60) ) / 60 )) min; destroying $id" >>"$LOG"
  terminate "$id"
  touch "$TRIAL_MARKER"
}

render_userdata() {
  local out=$1
  sed -e "s|@REGISTRY@|${REGISTRY:-none}|g" -e "s|@TAG@|${TAG:-latest}|g" \
    "$(dirname "$0")/aws-gpu-trial-userdata.sh" >"$out"
}

for type in "${TYPES[@]}"; do
  for pair in $SUBNETS; do
    subnet=${pair%%:*}
    az=${pair##*:}
    trial_this=$([[ $type == g7e.* && $TRIAL == 1 && ! -f $TRIAL_MARKER && -n ${TRIAL_AMI:-} ]] && echo yes || echo no)

    if [[ $trial_this == yes ]]; then
      ud=$(mktemp)
      render_userdata "$ud"
      out=$(aws ec2 run-instances --region "$REGION" \
        --image-id "$TRIAL_AMI" --instance-type "$type" --subnet-id "$subnet" \
        --security-group-ids "$SECURITY_GROUP" --iam-instance-profile "Name=$PROFILE" \
        --count 1 --user-data "file://$ud" \
        --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=g7e-trial},{Key=purpose,Value=first-g7e-trial}]" \
        --query 'Instances[0].InstanceId' --output text 2>&1)
      rm -f "$ud"
    else
      out=$(aws ec2 run-instances --region "$REGION" \
        --image-id "$AMI" --instance-type "$type" --subnet-id "$subnet" \
        --security-group-ids "$SECURITY_GROUP" --iam-instance-profile "Name=$PROFILE" \
        --count 1 \
        --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=capacity-probe},{Key=purpose,Value=quota-and-capacity-check}]" \
        --query 'Instances[0].InstanceId' --output text 2>&1)
    fi

    case "$out" in
      i-*)
        if [[ $trial_this == yes ]]; then
          echo "$(date -Is) $type $az LAUNCHED $out — held for the trial" >>"$LOG"
          run_trial "$out"
        else
          echo "$(date -Is) $type $az LAUNCHED $out — terminating" >>"$LOG"
          terminate "$out"
        fi
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

#!/usr/bin/env bash
# #16 acceptance evidence on the GPU host (generation/docs/llm.md section 7).
#
# Usage (with the pinned server running; every site-specific value is an argument):
#   generation/llm/host/evidence.sh --out <restricted>/llm-evidence --allow <station-net CIDR> \
#       [--base-url http://<station-net address>:<port>] [--model-dir <model-dir> --vllm-env <vllm-env>] \
#       [--calls 300] [--repeat 50] [--run-id DEMO-llm-bench-gpu-1] [--environment <label>] \
#       [--messages realistic=<file> --messages worst_case=<file>] [--skip-tests] [--dry-run]
#
# Writes into --out (refused inside a git work tree; mode 0700):
#   refusal-revision.{exit,log}, refusal-weights.{exit,log}
#       llm_server check on two throw-away copies of <model-dir> (symbolic links to the real
#       files): one whose download metadata names another revision, one with a weight shard
#       replaced by a sparse file of the same size. Both must be refused (exit 2).
#   bench/        llm_bench: --calls per prompt profile (realistic and worst case) through the
#                 real client against the running server, and --repeat same-seed pairs
#   ss-listening.txt, ss-established.txt, allowed.txt
#                 the ports this host listens on and TCP samples every 5 s during the
#                 benchmark; a connection this host opened to a peer outside loopback and
#                 --allow is an outbound connection
#   slow-cap.{exit,log}, seed.{exit,log}
#                 the 40.5-s slowed-server test and the seed tests (pytest)
#   acceptance.md the #16 acceptance criteria with PASS, FAIL or PENDING (llm_host report)
#
# Nothing here uses the network beyond the server: the offline variables are exported and
# llm_bench refuses every other host. AV_PY overrides the interpreter of the repository
# tools (default: `uv run --project generation python`).
set -euo pipefail

usage() {
    sed -n '4,8p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

REPO="${AV_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}"
OUT="${AV_EVIDENCE_OUT:-}"
BASE_URL="${AV_LLM_URL:-}"
MODEL_DIR="${AV_MODEL_DIR:-}"
VLLM_ENV="${AV_VLLM_ENV:-}"
CALLS=300
REPEAT=50
RUN_ID=DEMO-llm-bench-gpu-1
ENVIRONMENT=""
SKIP_TESTS=0
DRY_RUN=0
ALLOW=()
MESSAGES=()
read -r -a AV_PY <<< "${AV_PY:-uv run --project generation python}"

while [ "$#" -gt 0 ]; do
    case "$1" in
        --out) OUT="$2"; shift 2 ;;
        --allow) ALLOW+=("$2"); shift 2 ;;
        --base-url) BASE_URL="$2"; shift 2 ;;
        --model-dir) MODEL_DIR="$2"; shift 2 ;;
        --vllm-env) VLLM_ENV="$2"; shift 2 ;;
        --calls) CALLS="$2"; shift 2 ;;
        --repeat) REPEAT="$2"; shift 2 ;;
        --run-id) RUN_ID="$2"; shift 2 ;;
        --environment) ENVIRONMENT="$2"; shift 2 ;;
        --messages) MESSAGES+=(--messages "$2"); shift 2 ;;
        --skip-tests) SKIP_TESTS=1; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "evidence: unknown argument $1" >&2; usage >&2; exit 2 ;;
    esac
done
if [ -z "$OUT" ]; then
    echo "evidence: --out is required" >&2
    usage >&2
    exit 2
fi

run() {
    printf '+'
    printf ' %q' "$@"
    printf '\n'
    if [ "$DRY_RUN" = 0 ]; then
        "$@"
    fi
}

# Run a step whose exit code is evidence: <name>.exit and <name>.log in OUT.
record() {
    local name="$1"
    shift
    printf '+'
    printf ' %q' "$@"
    printf '  > %s.log\n' "$name"
    if [ "$DRY_RUN" = 0 ]; then
        local code=0
        "$@" > "$OUT/$name.log" 2>&1 || code=$?
        echo "$code" > "$OUT/$name.exit"
        echo "$name: exit $code"
    fi
}

# The output directory holds host addresses: never inside a git work tree.
probe="$OUT"
while [ ! -d "$probe" ]; do
    probe="$(dirname "$probe")"
done
if git -C "$probe" rev-parse --is-inside-work-tree > /dev/null 2>&1; then
    echo "evidence: $OUT is inside a git work tree; use restricted storage" >&2
    exit 2
fi

cd "$REPO"
eval "$("${AV_PY[@]}" -m av_generation.llm_host pins)"
BASE_URL="${BASE_URL:-http://$DEFAULT_HOST:$DEFAULT_PORT}"
ENVIRONMENT="${ENVIRONMENT:-$HARDWARE_LABEL}"
if [ -z "$ENVIRONMENT" ]; then
    echo "evidence: the manifest's hardware is $HARDWARE_STATUS; run bringup.sh first" \
         "(or give --environment)" >&2
    exit 2
fi
export UV_OFFLINE=1 HF_HUB_OFFLINE=1 HF_DATASETS_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
       HF_HUB_DISABLE_TELEMETRY=1 VLLM_NO_USAGE_STATS=1 VLLM_DO_NOT_TRACK=1 DO_NOT_TRACK=1
run mkdir -p "$OUT"
run chmod 700 "$OUT"
if [ "$DRY_RUN" = 0 ]; then
    printf '%s\n' "${ALLOW[@]}" > "$OUT/allowed.txt"
fi

echo "== 1. start refusals (a different revision, a changed weight shard)"
if [ -n "$MODEL_DIR" ] && [ -n "$VLLM_ENV" ]; then
    for kind in revision weights; do
        copy="$OUT/tamper-$kind"
        run mkdir -p "$copy"
        if [ "$DRY_RUN" = 0 ]; then
            shopt -s dotglob
            for file in "$MODEL_DIR"/*; do
                if [ "$(basename "$file")" != .cache ]; then
                    ln -s "$file" "$copy/"
                fi
            done
            shopt -u dotglob
            cp -R "$MODEL_DIR/.cache" "$copy/.cache"
            if [ "$kind" = revision ]; then
                other="$(printf '0%.0s' $(seq 1 ${#MODEL_REVISION}))"
                for meta in "$copy"/.cache/huggingface/download/*.metadata; do
                    { echo "$other"; tail -n +2 "$meta"; } > "$meta.tmp"
                    mv "$meta.tmp" "$meta"
                done
            else
                rm "$copy/$SMALLEST_SHARD"
                truncate -s "$SMALLEST_SHARD_BYTES" "$copy/$SMALLEST_SHARD"
            fi
        fi
        record "refusal-$kind" "${AV_PY[@]}" -m av_generation.llm_server check \
            --model-dir "$copy" --vllm "$VLLM_ENV/bin/vllm"
        run rm -rf "$copy"
    done
else
    echo "skipped: give --model-dir and --vllm-env (the criterion stays PENDING)"
fi

echo "== 2. benchmark with network samples"
SAMPLER=""
if [ "$DRY_RUN" = 0 ]; then
    ss -Htln > "$OUT/ss-listening.txt"
    : > "$OUT/ss-established.txt"
    (
        while true; do
            { echo "# $(date -u +%Y-%m-%dT%H:%M:%SZ)"; ss -Htn state established; } \
                >> "$OUT/ss-established.txt"
            sleep 5
        done
    ) &
    SAMPLER=$!
    trap 'kill "$SAMPLER" 2> /dev/null || true' EXIT
else
    echo "+ ss -Htln; every 5 s: ss -Htn state established"
fi
record bench "${AV_PY[@]}" -m av_generation.llm_bench --base-url "$BASE_URL" \
    --environment "$ENVIRONMENT" --run-id "$RUN_ID" --calls "$CALLS" --repeat "$REPEAT" \
    --out-dir "$OUT/bench" "${MESSAGES[@]+"${MESSAGES[@]}"}"
if [ -n "$SAMPLER" ]; then
    kill "$SAMPLER" 2> /dev/null || true
    { echo "# $(date -u +%Y-%m-%dT%H:%M:%SZ) final"; ss -Htn state established; } \
        >> "$OUT/ss-established.txt"
fi

echo "== 3. timeout cap and seed tests"
if [ "$SKIP_TESTS" = 0 ]; then
    record slow-cap "${AV_PY[@]}" -m pytest --import-mode=importlib -p no:cacheprovider \
        tests/generation/test_llm_client.py -k slow
    record seed "${AV_PY[@]}" -m pytest --import-mode=importlib -p no:cacheprovider \
        tests/generation/test_seed.py
fi

echo "== 4. acceptance"
run "${AV_PY[@]}" -m av_generation.llm_host report --out-dir "$OUT"

#!/usr/bin/env bash
# Bring-up of the pinned LLM server on the GPU host (#16; generation/docs/llm.md section 7).
#
# Usage (from any directory; every site-specific value is an argument or AV_* variable):
#   generation/llm/host/bringup.sh --vllm-env <vllm-env> --model-dir <model-dir> \
#       --host <station-net address> [--port <port>] [--python <python3>] [--hf <hf>] \
#       [--skip-install] [--skip-download] [--record-hardware] [--dry-run]
#
# Steps (each prints its command first):
#   0. uv sync of this repository's generation project              (network: package index)
#   1. refuse a GPU with less VRAM than the manifest's minimum (bf16 needs a 24 GB card)
#   2. create the vLLM environment and install the pinned vLLM       (network: package index)
#   3. hf download of the pinned revision into <model-dir>           (network: model hub)
#      From here on nothing uses the network: the offline variables below are exported.
#   4. llm_manifest verify      (files, sizes, revision, blobs, chat template, weight SHA-256)
#   5. llm_manifest record-hardware, then llm_host update-config (only while the manifest's
#      hardware is pending, or with --record-hardware); commit both files afterwards
#   6. llm_server check         (every refusal check the server start runs)
#   7. print the serve command, the freeze-record commands and the next steps
#
# The model ID, revision, vLLM version, port and minimum VRAM are read from
# generation/llm/manifest.json and server-config.json (python -m av_generation.llm_host pins),
# never written here. AV_PY overrides the interpreter of the repository tools (default:
# `uv run --project generation python`).
set -euo pipefail

usage() {
    sed -n '4,7p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

REPO="${AV_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}"
VLLM_ENV="${AV_VLLM_ENV:-}"
MODEL_DIR="${AV_MODEL_DIR:-}"
HOST="${AV_LLM_HOST:-}"
PORT="${AV_LLM_PORT:-}"
PYTHON="${AV_PYTHON:-python3}"
HF="${AV_HF:-}"
SKIP_INSTALL=0
SKIP_DOWNLOAD=0
RECORD=0
DRY_RUN=0
read -r -a AV_PY <<< "${AV_PY:-uv run --project generation python}"

while [ "$#" -gt 0 ]; do
    case "$1" in
        --vllm-env) VLLM_ENV="$2"; shift 2 ;;
        --model-dir) MODEL_DIR="$2"; shift 2 ;;
        --host) HOST="$2"; shift 2 ;;
        --port) PORT="$2"; shift 2 ;;
        --python) PYTHON="$2"; shift 2 ;;
        --hf) HF="$2"; shift 2 ;;
        --skip-install) SKIP_INSTALL=1; shift ;;
        --skip-download) SKIP_DOWNLOAD=1; shift ;;
        --record-hardware) RECORD=1; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "bringup: unknown argument $1" >&2; usage >&2; exit 2 ;;
    esac
done
if [ -z "$VLLM_ENV" ] || [ -z "$MODEL_DIR" ]; then
    echo "bringup: --vllm-env and --model-dir are required" >&2
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

cd "$REPO"

echo "== 0. repository tools"
run uv sync --project generation --locked

# Pins from the committed (and checked) manifest and server config: MODEL_ID,
# MODEL_REVISION, VLLM_VERSION, DEFAULT_HOST, DEFAULT_PORT, MAX_MODEL_LEN, PRECISION,
# HARDWARE_STATUS, WEIGHTS_BYTES, MIN_VRAM_MIB, ...
eval "$("${AV_PY[@]}" -m av_generation.llm_host pins)"
HOST="${HOST:-$DEFAULT_HOST}"
PORT="${PORT:-$DEFAULT_PORT}"
VLLM="$VLLM_ENV/bin/vllm"
HF="${HF:-$VLLM_ENV/bin/hf}"
# shellcheck disable=SC2153  # MODEL_ID and the others come from the eval above
echo "pinned: $MODEL_ID revision $MODEL_REVISION, vLLM $VLLM_VERSION, $PRECISION," \
     "max_model_len $MAX_MODEL_LEN, weights $WEIGHTS_BYTES bytes; GPU minimum $MIN_VRAM_MIB MiB"

echo "== 1. GPU memory"
if [ "$DRY_RUN" = 0 ]; then
    vram="$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -n 1)"
    vram="${vram%%.*}"
    vram="${vram// /}"
    if [ -z "$vram" ] || [ "$vram" -lt "$MIN_VRAM_MIB" ]; then
        echo "REFUSED: the GPU reports ${vram:-no} MiB; the frozen $PRECISION configuration" \
             "needs at least $MIN_VRAM_MIB MiB (weights alone are $WEIGHTS_BYTES bytes)." \
             "Quantizing would change the model, which the protocol does not allow." >&2
        exit 3
    fi
    echo "GPU memory $vram MiB >= $MIN_VRAM_MIB MiB"
else
    echo "+ nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits  (>= $MIN_VRAM_MIB MiB)"
fi

echo "== 2. vLLM environment"
if [ "$SKIP_INSTALL" = 0 ]; then
    if [ ! -x "$VLLM_ENV/bin/python" ]; then
        run "$PYTHON" -m venv "$VLLM_ENV"
    fi
    run "$VLLM_ENV/bin/pip" install "vllm==$VLLM_VERSION"
fi

echo "== 3. download of the pinned revision (the last step that uses the network)"
if [ "$SKIP_DOWNLOAD" = 0 ]; then
    if [ "$DRY_RUN" = 0 ] && [ ! -x "$HF" ]; then
        echo "bringup: $HF not found; give the Hugging Face CLI with --hf" >&2
        exit 2
    fi
    run "$HF" download "$MODEL_ID" --revision "$MODEL_REVISION" --local-dir "$MODEL_DIR"
fi

export UV_OFFLINE=1 HF_HUB_OFFLINE=1 HF_DATASETS_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
       HF_HUB_DISABLE_TELEMETRY=1 VLLM_NO_USAGE_STATS=1 VLLM_DO_NOT_TRACK=1 DO_NOT_TRACK=1

echo "== 4. verify the download"
run "${AV_PY[@]}" -m av_generation.llm_manifest verify --model-dir "$MODEL_DIR"

echo "== 5. hardware record (hardware is $HARDWARE_STATUS)"
if [ "$HARDWARE_STATUS" = pending ] || [ "$RECORD" = 1 ]; then
    run "${AV_PY[@]}" -m av_generation.llm_manifest record-hardware --vllm "$VLLM"
    run "${AV_PY[@]}" -m av_generation.llm_host update-config
    run git status --short generation/llm
fi

echo "== 6. server checks"
run "${AV_PY[@]}" -m av_generation.llm_server check --model-dir "$MODEL_DIR" --vllm "$VLLM" \
    --host "$HOST" --port "$PORT"

echo "== 7. next steps"
if [ "$DRY_RUN" = 1 ]; then
    echo "(dry run: the commands above were printed, not run)"
fi
cat <<EOF
The pinned server passes every start check. Start it as a service
(generation/llm/host/av-llm.service.example) or in the foreground:

  ${AV_PY[*]} -m av_generation.llm_server serve --model-dir $MODEL_DIR --vllm $VLLM \\
      --host $HOST --port $PORT --timing-log <restricted>/llm-server/timing.jsonl --run-id <run id>

Then, in order (generation/docs/llm.md section 7):
  - commit generation/llm/manifest.json and generation/llm/server-config.json (the hardware
    record) in a pull request that says "Part of #16";
  - generation/llm/host/evidence.sh --out <restricted>/llm-evidence --allow <station-net CIDR> \\
        --model-dir $MODEL_DIR --vllm-env $VLLM_ENV
  - fill the G4 freeze draft's GPU-host items in the restricted copy of the draft:
        ${AV_PY[*]} -m av_generation.llm_host freeze-commands --draft <restricted>/freeze-draft.json
EOF

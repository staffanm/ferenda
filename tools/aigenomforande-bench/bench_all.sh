#!/bin/bash
# Run the ai-genomforande benchmark: 20 props x 4 endpoint configs.
# Berget configs run their props with modest concurrency; local is serial.
# usage: bench_all.sh <config>   where config = local|mistral|kimi|gptoss
set -u
cd /home/staffan/repos/ferenda
SP=/tmp/claude-1000/-home-staffan-repos-ferenda/f1c29770-8aae-4eea-bfb6-6f9451fdae6f/scratchpad
PROPS="3 16 28 43 84 108 118 124 129 146 159 183 186 202 240 253 262 265 278 303"
CFG=$1
case $CFG in
  local)   export LLM_BASE_URL=http://127.0.0.1:8123/v1 BERGET_MODEL=qwen3.6-35b-a3b; PAR=1;;
  mistral) export LLM_BASE_URL=https://api.berget.ai/v1 BERGET_MODEL=mistralai/Mistral-Medium-3.5-128B; PAR=4;;
  kimi)    export LLM_BASE_URL=https://api.berget.ai/v1 BERGET_MODEL=moonshotai/Kimi-K2.6; PAR=4;;
  gptoss)  export LLM_BASE_URL=https://api.berget.ai/v1 BERGET_MODEL=openai/gpt-oss-120b; PAR=4;;
  glm)     export LLM_BASE_URL=https://api.berget.ai/v1 BERGET_MODEL=zai-org/GLM-5.2; PAR=4;;
  gemma)   export LLM_BASE_URL=https://api.berget.ai/v1 BERGET_MODEL=google/gemma-4-31B-it; PAR=4;;
  *) echo "unknown config $CFG" >&2; exit 1;;
esac
mkdir -p "$SP/bench/$CFG"
echo "$PROPS" | tr ' ' '\n' | xargs -P $PAR -I{} \
  .venv/bin/python "$SP/bench_one.py" "prop/2025-26-{}" "$SP/bench/$CFG/{}.json"
echo "config $CFG done"

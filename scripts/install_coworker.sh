#!/usr/bin/env bash
# Install coworker_llm CLIs on PATH and slash command markdown into ~/.claude/commands/.
set -euo pipefail

cd "$(dirname "$0")/.."

uv tool install --force .

mkdir -p "$HOME/.claude/commands"
cp coworker_llm/claude_commands/ask-llm.md \
   coworker_llm/claude_commands/llm-write.md \
   coworker_llm/claude_commands/extract-chat.md \
   "$HOME/.claude/commands/"

echo
echo 'Installed. Verify with:'
echo '  which ask-llm llm-write extract-chat'
echo '  ls ~/.claude/commands/ | grep -E "(ask-llm|llm-write|extract-chat)"'

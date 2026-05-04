#!/usr/bin/env bash
# Install coworker_llm CLIs (editable, so source edits take effect immediately
# without reinstalling) and copy slash command markdown into ~/.claude/commands/.
set -euo pipefail

cd "$(dirname "$0")/.."

uv tool install --editable --force --reinstall .

mkdir -p "$HOME/.claude/commands"
cp coworker_llm/claude_commands/ask-llm.md \
   coworker_llm/claude_commands/llm-write.md \
   coworker_llm/claude_commands/extract-chat.md \
   "$HOME/.claude/commands/"

echo
echo 'Installed (editable). Source edits take effect immediately — no reinstall needed.'
echo 'Verify with:'
echo '  which ask-llm llm-write extract-chat'
echo '  ls ~/.claude/commands/ | grep -E "(ask-llm|llm-write|extract-chat)"'

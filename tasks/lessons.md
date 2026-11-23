# Lessons

- **Edit the folder the session is opened in.** If a prompt names a different folder than the
  working directory (e.g. a `claude_run/repo` copy), ask which one before editing. Don't assume
  the path in a pasted prompt beats the workspace the user has open. (2026-11-22)
- **Copies of a repo may differ.** Before moving edits between folders, diff them: here the
  files were black-formatted and had different docstrings, so whole-file copies would have
  overwritten the user's version.

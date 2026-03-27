<!-- scroll:start -->
## Project Knowledge (scroll)

*Extracted from `svx` git history.*

### Decisions

- **DEC-001**: Changed from binary block/allow to advisory denies with risk classification (high)
  - Binary decisions were too restrictive and lacked nuance. Risk classification allows the system to provide helpful guidance while still protecting against catastrophic operations. Advisory denies give users actionable information rather than just blocking them.
- **DEC-002**: Introduced project scoping with .svx/ directory initialization (high)
  - SVX should only guard opted-in projects rather than blocking everything globally. This provides better user control and reduces false positives for operations in unrelated directories.
- **DEC-003**: Expanded safety assessment from shell commands to file edit operations (high)
  - File operations can be as dangerous as shell commands. Config file changes, large modifications, and edits to untracked files all pose risks that should be assessed before execution.
- **DEC-004**: Implemented fail-open error handling for hook integration
  - Fail-open prevents the safety system from becoming a blocker when it encounters unexpected input or errors. Better to allow potentially risky operations than to break the user's workflow entirely due to parsing issues.

### Learnings

- **LRN-001**: Claude Code requires specific hook output format for permission decisions (high)
  - Claude Code expects the specific format `{"hookSpecificOutput": {"permissionDecision": "deny"}}` for hook responses. The API contract is strict about this structure.

<!-- scroll:end -->

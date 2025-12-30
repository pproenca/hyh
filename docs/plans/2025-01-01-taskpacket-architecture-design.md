# TaskPacket Architecture Design

**Goal:** Redesign hyh's planning and orchestration to distribute self-contained work packets to agents, eliminating the need for agents to load the entire plan.

**Key Insight:** Each agent receives ONLY its TaskPacket - no plan file, no other task context. Like a principal engineer distributing work: IC7 (orchestrator) → IC6 (implementers) → IC5 (reviewers).

---

## 1. TaskPacket Structure

The fundamental unit of work - a self-contained work order:

```python
from enum import Enum
from msgspec import Struct

class AgentModel(str, Enum):
    HAIKU = "haiku"    # Quick tasks, verification, simple edits
    SONNET = "sonnet"  # Standard implementation work
    OPUS = "opus"      # Complex architecture, difficult debugging


class TaskPacket(Struct, frozen=True, forbid_unknown_fields=True):
    """Complete work packet for an agent. Agent receives ONLY this."""

    # Identity
    id: str
    description: str
    role: str | None = None       # "implementer", "reviewer", "architect"
    model: AgentModel = AgentModel.SONNET

    # Scope boundaries
    files_in_scope: tuple[str, ...] = ()     # Files agent CAN touch
    files_out_of_scope: tuple[str, ...] = () # Explicit "don't touch"

    # Interface contract
    input_context: str = ""      # What this task receives
    output_contract: str = ""    # What this task must produce

    # Implementation
    instructions: str = ""       # Step-by-step TDD guidance
    constraints: str = ""        # Patterns to follow, things to avoid

    # Tool permissions
    tools: tuple[str, ...] = ()  # "Read", "Edit", "Bash", "Grep"

    # Verification
    verification_commands: tuple[str, ...] = ()  # Project-specific test commands
    success_criteria: str = ""                   # How to know you're done

    # Artifacts
    artifacts_to_read: tuple[str, ...] = ()   # Files from prior tasks
    artifacts_to_write: tuple[str, ...] = ()  # Files for downstream tasks
```

---

## 2. XML Plan Format

The orchestrator produces XML that maps directly to TaskPackets:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<plan goal="Implement user authentication system">

  <phases>
    <phase id="explore" type="investigation">
      <!-- Orchestrator does this directly, no subagents -->
      <objectives>
        - Read auth/* files and document current patterns
        - Identify integration points with existing user service
        - List security requirements from spec
      </objectives>
      <output>exploration-summary.md</output>
    </phase>

    <phase id="implement" type="execution">
      <!-- TaskPackets for subagents -->
    </phase>

    <phase id="verify" type="verification">
      <!-- Reviewer TaskPackets -->
    </phase>
  </phases>

  <dependencies>
    <dep from="T002" to="T001"/>
    <dep from="T003" to="T001"/>
    <dep from="T-review" to="T002,T003"/>
  </dependencies>

  <task id="T001" role="implementer" model="sonnet">
    <description>Create JWT token generation service</description>

    <tools>Read, Edit, Bash, Grep</tools>

    <scope>
      <include>src/auth/token.py</include>
      <include>tests/auth/test_token.py</include>
      <exclude>src/auth/session.py</exclude>
    </scope>

    <interface>
      <input>User credentials schema: {user_id: str, password: str}</input>
      <output>TokenService with generate(user_id) -> JWT string</output>
    </interface>

    <instructions><![CDATA[
TDD Workflow:

1. Write failing test in tests/auth/test_token.py:
   - test_generate_token_returns_valid_jwt()
   - test_token_contains_user_id_claim()
   - test_token_expires_after_configured_time()

2. Run tests, verify FAIL (no implementation yet)

3. Implement src/auth/token.py:
   - TokenService class
   - generate(user_id: str) -> str method
   - Use HS256 signing from settings.SECRET_KEY
   - Set expiry from settings.TOKEN_EXPIRY_SECONDS

4. Run tests, verify PASS

5. Write artifact documenting the API for downstream tasks
    ]]></instructions>

    <constraints><![CDATA[
- Use existing jwt library, don't add new dependencies
- Follow existing code patterns in src/auth/
- Don't modify any files outside scope
    ]]></constraints>

    <anti_overfitting><![CDATA[
Write a high-quality, general-purpose solution using standard patterns.
Do NOT:
- Hard-code values for specific test inputs
- Create solutions that only work for test cases
- Create helper scripts or workarounds

The implementation must work correctly for ALL valid inputs.
    ]]></anti_overfitting>

    <verification>
      <command>pytest tests/auth/test_token.py -v</command>
      <command>ruff check src/auth/token.py</command>
    </verification>

    <success>All tests pass, ruff clean, artifact written</success>

    <artifacts>
      <write>.claude/artifacts/T001-token-api.md</write>
    </artifacts>
  </task>

  <task id="T002" role="implementer" model="sonnet">
    <description>Create session management using token service</description>

    <tools>Read, Edit, Bash, Grep</tools>

    <scope>
      <include>src/auth/session.py</include>
      <include>tests/auth/test_session.py</include>
    </scope>

    <interface>
      <input>TokenService API from T001 artifact</input>
      <output>SessionManager with create_session(), validate_session()</output>
    </interface>

    <instructions><![CDATA[
1. Read the T001 artifact to understand TokenService API
2. Write failing tests for session management
3. Implement SessionManager using TokenService
4. Run tests, verify PASS
    ]]></instructions>

    <constraints>Use TokenService from src/auth/token.py</constraints>

    <verification>
      <command>pytest tests/auth/test_session.py -v</command>
      <command>ruff check src/auth/session.py</command>
    </verification>

    <success>All tests pass, integrates correctly with TokenService</success>

    <artifacts>
      <read>.claude/artifacts/T001-token-api.md</read>
      <write>.claude/artifacts/T002-session-api.md</write>
    </artifacts>
  </task>

  <task id="T-review" role="reviewer" model="haiku">
    <description>Verify authentication implementation quality</description>

    <tools>Read, Grep, Glob, Bash</tools>

    <scope>
      <include>src/auth/</include>
      <include>tests/auth/</include>
    </scope>

    <interface>
      <input>Implementations from T001, T002</input>
      <output>Verification report: PASS or FAIL with issues</output>
    </interface>

    <instructions><![CDATA[
Review the implementation for:
1. Hard-coded values that only satisfy test cases
2. Missing edge case handling
3. Security vulnerabilities (token signing, expiry)
4. Does it match the interface contracts in artifacts?
5. Pattern consistency with codebase conventions

Run full test suite and check coverage.

Report format:
<verification>
  <status>PASS|FAIL</status>
  <issues>
    <issue severity="high|medium|low">Description</issue>
  </issues>
  <recommendations>Optional improvements</recommendations>
</verification>
    ]]></instructions>

    <constraints>DO NOT modify any code. Read-only review.</constraints>

    <verification>
      <command>pytest tests/auth/ -v --cov=src/auth</command>
    </verification>

    <success>Report written with clear PASS/FAIL status</success>

    <artifacts>
      <read>.claude/artifacts/T001-token-api.md</read>
      <read>.claude/artifacts/T002-session-api.md</read>
      <write>.claude/artifacts/verification-report.md</write>
    </artifacts>
  </task>

</plan>
```

---

## 3. Execution Flow

```
                    PHASE 0: SESSION START
                    ───────────────────────
                    SessionStart hook → hyh session-start
                    Load workflow state, inject context
                              │
                              ▼
                    PHASE 1: EXPLORE
                    ────────────────
                    Orchestrator directly (no subagents)
                    - Read codebase patterns
                    - Extended thinking (ultrathink)
                    - Write exploration-summary.md
                              │
                              ▼
                    PHASE 2: PLAN
                    ─────────────
                    Orchestrator creates XML plan
                    hyh plan import --file feature.xml
                    Get user approval
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    PHASE 3: EXECUTE                             │
│                                                                 │
│  Orchestrator spawns subagents (parallel where deps allow)      │
│                                                                 │
│  ┌─────────┐    ┌─────────┐    ┌─────────┐                     │
│  │Agent A  │    │Agent B  │    │Agent C  │                     │
│  │(T001)   │    │(T002)   │    │(T003)   │                     │
│  └────┬────┘    └────┬────┘    └────┬────┘                     │
│       │              │              │                           │
│       ▼              ▼              ▼                           │
│  hyh task claim  (waits for   (waits for                        │
│       │           T001)        T001)                            │
│       ▼                                                         │
│  Receives ONLY    ─────────────────────────────                 │
│  TaskPacket T001  │ Agent sees NOTHING about  │                 │
│  (no plan file!)  │ T002, T003, or plan file  │                 │
│                   ─────────────────────────────                 │
│       │                                                         │
│       ▼                                                         │
│  Execute with hooks:                                            │
│  - PostToolUse: "Did you run tests?"                           │
│  - SubagentStop: "All criteria met?"                           │
│       │                                                         │
│       ▼                                                         │
│  Write artifact                                                 │
│  hyh task complete --id T001                                    │
│       │                                                         │
│       └─────────► T002, T003 now claimable                     │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
                    PHASE 4: VERIFY
                    ───────────────
                    Reviewer subagent (fresh context)
                    Reads artifacts, not impl details
                    Reports PASS/FAIL
                              │
                    ┌─────────┴─────────┐
                    ▼                   ▼
                  PASS               FAIL
                    │                   │
                    ▼                   ▼
              PHASE 5:           Spawn fix task
              SYNTHESIZE         or report to user
                    │
                    ▼
              Create PR
```

---

## 4. Claude Code Hooks (Prompt-Based, Codebase Agnostic)

```json
{
  "hooks": {
    "SessionStart": [{
      "hooks": [{
        "type": "command",
        "command": "hyh session-start",
        "timeout": 5
      }]
    }],

    "PostToolUse": [{
      "matcher": "Write|Edit",
      "hooks": [{
        "type": "prompt",
        "prompt": "You just modified code. Have you run the relevant tests to verify your changes don't break anything? If not, run them now before proceeding."
      }]
    }],

    "SubagentStop": [{
      "hooks": [{
        "type": "prompt",
        "prompt": "Before completing, verify:\n1. Have you run ALL verification commands from your TaskPacket?\n2. Do ALL tests pass?\n3. Have you written the required artifacts?\n\nIf any answer is NO, continue working. Do NOT stop until all criteria are verified."
      }]
    }],

    "Stop": [{
      "hooks": [{
        "type": "prompt",
        "prompt": "Before stopping, confirm:\n1. All TaskPacket success criteria met?\n2. All verification commands run and passing?\n3. All artifacts written?\n4. Called 'hyh task complete' if applicable?\n\nIf any condition fails, continue working."
      }]
    }],

    "PreCompact": [{
      "hooks": [{
        "type": "command",
        "command": "hyh context preserve",
        "timeout": 10
      }]
    }]
  }
}
```

---

## 5. Custom Agent Definitions

### `.claude/agents/implementer.md`

```markdown
---
name: implementer
description: Feature implementation with strict TDD
tools: Read, Edit, Bash, Grep, Glob
model: sonnet
---

You execute implementation tasks following strict TDD.

<task_packet>
$TASK_PACKET
</task_packet>

CRITICAL RULES:
1. Stay within scope boundaries - do NOT touch files outside your scope
2. Follow TDD: test first → fail → implement → pass
3. Write artifacts before completing
4. Run ALL verification commands
5. Call `hyh task complete --id $TASK_ID` when done

Your context is ISOLATED. You don't know about other tasks.
Focus ONLY on your TaskPacket objectives.
```

### `.claude/agents/reviewer.md`

```markdown
---
name: reviewer
description: Code review and verification (read-only)
tools: Read, Grep, Glob, Bash
model: haiku
---

You verify implementations WITHOUT modifying code.

<task_packet>
$TASK_PACKET
</task_packet>

Review checklist:
1. Hard-coded values that only satisfy tests?
2. Missing edge case handling?
3. Security vulnerabilities?
4. Pattern consistency with codebase?
5. Interface contracts honored?

Report format:
<verification>
  <status>PASS|FAIL</status>
  <issues>...</issues>
</verification>
```

---

## 6. Daemon RPC API Changes

### Current: Returns minimal task info
```python
{"command": "task_claim", "worker_id": "worker-abc"}
→ {"task": {"id": "T001", "description": "...", "instructions": "..."}}
```

### New: Returns complete TaskPacket
```python
{"command": "task_claim", "worker_id": "worker-abc"}
→ {
    "task": {
        "id": "T001",
        "description": "Create JWT token service",
        "role": "implementer",
        "model": "sonnet",
        "files_in_scope": ["src/auth/token.py", "tests/auth/test_token.py"],
        "files_out_of_scope": ["src/auth/session.py"],
        "input_context": "User credentials schema: {user_id: str}",
        "output_contract": "TokenService with generate(user_id) -> JWT",
        "instructions": "1. Write failing test...",
        "constraints": "Use existing jwt library",
        "tools": ["Read", "Edit", "Bash", "Grep"],
        "verification_commands": ["pytest tests/auth/", "ruff check"],
        "success_criteria": "All tests pass, ruff clean",
        "artifacts_to_read": [],
        "artifacts_to_write": [".claude/artifacts/T001-api.md"]
    }
}
```

### New Commands

```python
# Import XML plan
{"command": "plan_import", "content": "<plan>...</plan>", "format": "xml"}
→ {"status": "ok", "task_count": 5, "validation_errors": []}

# Get specific task (for orchestrator monitoring)
{"command": "task_get", "task_id": "T001"}
→ {"task": {...}, "status": "completed", "artifacts_written": [...]}

# Preserve context (for PreCompact hook)
{"command": "context_preserve"}
→ Writes .claude/progress.txt with current state
```

---

## 7. Separation of Concerns

| Concern | hyh Daemon | Claude Code |
|---------|-----------|-------------|
| Task state (claim/complete) | ✓ | |
| DAG dependencies | ✓ | |
| TaskPacket storage | ✓ | |
| Spawning subagents | | ✓ (Task tool) |
| Running tests | | ✓ (Agent decides) |
| Verifying completion | | ✓ (Hooks + prompts) |
| Artifact file operations | | ✓ (Agent reads/writes) |
| Context preservation | ✓ (command) | ✓ (PreCompact hook) |

**Daemon stays minimal:** State machine + RPC. Claude does the intelligent work.

---

## 8. Key Benefits Over Current Design

| Current Problem | TaskPacket Solution |
|-----------------|---------------------|
| Agents load entire plan | Agents receive ONLY their TaskPacket |
| Instructions as blob string | Structured fields with clear contracts |
| No artifact coordination | Explicit artifacts_to_read/write |
| No scope boundaries | files_in_scope/out_of_scope enforced |
| Generic test hooks | Project-specific verification_commands |
| No model selection | AgentModel per task (haiku/sonnet/opus) |
| No role specialization | role field maps to custom agents |

---

## 9. Implementation Tasks

1. **Add TaskPacket struct** to `src/hyh/plan.py`
2. **Add XML parser** for new plan format
3. **Update daemon RPC** to return TaskPacket on claim
4. **Add `plan_import` command** with XML support
5. **Add `context_preserve` command** for PreCompact hook
6. **Create custom agent definitions** in `.claude/agents/`
7. **Update hook configurations** in settings template
8. **Update orchestrator prompts** in plugin commands

---

## References

- Anthropic's orchestrator-worker pattern documentation
- Claude Code hooks and custom agents documentation
- TDD workflow best practices

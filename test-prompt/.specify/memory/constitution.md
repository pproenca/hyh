<!--
Sync Impact Report - Constitution Update

Version Change: NONE → 1.0.0
Reason: Initial creation. Defined 5 principles focusing on code quality, testing standards, UX consistency, and performance requirements.

Modified Principles:
- N/A (Initial creation)

Added Sections:
- Core Principles (5 principles)
  1. Code Quality Principle
  2. Test-Driven Development
  3. UX Consistency
  4. Performance Standards
  5. Maintainability and Extensibility
- Development Workflow
- Code Review Process
- Governance

Removed Sections:
- N/A (Initial creation)

Template Consistency Check:
✅ plan-template.md - Consistent with Constitution Check section
✅ spec-template.md - Consistent with requirements definition section
✅ tasks-template.md - Test phases and task structure reflect principles
✅ checklist-template.md - Checklist format supports principle application
✅ agent-file-template.md - Supports development guidelines generation

Follow-up TODOs:
- None (all required fields are configured)

Commit Message Suggestion:
docs: Create constitution v1.0.0 (code quality, testing, UX, performance principles)
-->

# CC-WF-Studio Constitution

## Core Principles

### I. Code Quality Principle

**Required Standards**:

- All code must prioritize readability and be self-documenting
- Variable names, function names, and class names must use clear Japanese or English that expresses their purpose
- Magic numbers are prohibited; they must be defined as constants
- Code complexity must be kept within reasonable bounds, with refactoring performed as needed
- All public APIs and functions must have appropriate documentation comments

**Rationale**: Highly readable code reduces maintenance costs and improves overall team productivity. Clear naming conventions and documentation also reduce onboarding time for new members.

### II. Test-Driven Development (Required)

**Required Standards**:

- All new features must be developed test-first (Red-Green-Refactor cycle)
- Test coverage must be maintained at minimum 80%, with 100% targeted for critical business logic
- The following 3 types of tests must be appropriately implemented:
    - **Contract tests**: Public interface specifications for APIs and libraries
    - **Integration tests**: Coordination behavior between multiple components
    - **Unit tests**: Behavior of individual functions and methods
- Tests must be independently executable and must not depend on execution order
- When tests fail, implementation work must stop and root cause investigation and fixes must be prioritized

**Rationale**: Test-driven development achieves requirement clarification, design quality improvement, and regression prevention. Tests function as a safety net for development, enabling confident refactoring and deployment.

### III. UX Consistency

**Required Standards**:

- All user interfaces must follow consistent design patterns
- Error messages must be clear and indicate actionable steps (what happened, why it happened, how to resolve it)
- Loading states, success states, and error states must be explicitly displayed
- Accessibility must be considered including keyboard operation and screen readers
- Continuous improvements based on user feedback must be implemented
- Consistent input/output formats must be adopted across CLI, API, and GUI

**Rationale**: Consistent UX flattens the learning curve and improves user productivity and satisfaction. Ensuring accessibility allows us to provide value to more users.

### IV. Performance Standards

**Required Standards**:

- All API response times must target 200ms or less at the 95th percentile (p95)
- Database queries must use appropriate indexes and avoid N+1 problems
- Memory leaks must be prevented, with stable memory usage during long-running execution
- Performance-critical processes must undergo measurement and profiling
- Pagination, streaming, and batch processing must be applied when handling large data volumes
- Frontend must target initial load time within 3 seconds and time to interactive within 5 seconds

**Rationale**: Performance directly impacts user experience. Response delays increase user churn rates and harm business value. Early measurement and optimization ensure scalability.

### V. Maintainability and Extensibility

**Required Standards**:

- All features must be designed as independent libraries/modules
- Dependencies must be minimized to maintain loosely coupled architecture
- Configuration values must be separated from code and managed via environment variables or configuration files
- Logs must be output in structured format (JSON, etc.) to facilitate debugging and monitoring
- Versioning must follow semantic versioning (MAJOR.MINOR.PATCH)
- Breaking changes must include migration guides and deprecation periods

**Rationale**: Highly maintainable code reduces long-term development costs. Modularization enables feature reuse and parallel development, improving team productivity.

## Development Workflow

### Feature Development Process

1. **Specification**: Clearly define user stories and acceptance criteria (spec.md)
1. **Design**: Document technical approach and architecture (plan.md)
1. **Test Creation**: Implement acceptance criteria as test code
1. **Implementation**: Implement features until tests pass
1. **Review**: Conduct code review and quality checks
1. **Deployment**: Perform staged release and monitoring

### Branch Strategy

- Main branch: `master` or `main` (always maintain deployable state)
- Feature branches: `###-feature-name` (number and descriptive name)
- Utilize feature flags to maintain safe state even when merging incomplete features

## Code Review Process

### Required Review Items

All pull requests must be reviewed from the following perspectives:

1. **Constitution Compliance Check**: Does it comply with all 5 principles?
1. **Test Sufficiency**: Is there appropriate test coverage?
1. **Security**: Are there any vulnerabilities or security risks?
1. **Performance Impact**: Is there any performance degradation?
1. **Documentation**: Has necessary documentation been updated?

### Justification of Complexity

When introducing complexity that violates the constitution (e.g., multiple projects, complex abstractions):

- Clear explanation of necessity
- Reasons why simpler alternatives were considered and rejected
- Record in the "Complexity Tracking" section of the implementation plan (plan.md)

## Governance

### Position of the Constitution

This constitution takes precedence over all development practices. All members participating in the project are responsible for understanding and complying with this constitution.

### Amendment Process

Constitution amendments must follow these steps:

1. Document the amendment proposal (background, reason, scope of impact)
1. Team review and discussion
1. Consensus building (major changes require unanimous approval)
1. Develop transition plan (if there is impact on existing code)
1. Update version number and create release notes

### Compliance Review

- Verify constitution compliance on all pull requests
- Review constitution compliance status of existing code quarterly
- When violations are discovered, develop improvement plans and address them as priority

### Versioning Policy

- **MAJOR**: Removal or redefinition of principles without backward compatibility
- **MINOR**: Addition of new principles or significant expansion of existing principles
- **PATCH**: Non-essential changes such as clarification, wording improvements, typo corrections

**Version**: 1.0.0 | **Ratified**: 2025-11-01 | **Last Amended**: 2025-11-01

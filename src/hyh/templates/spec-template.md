# Feature Specification: [Feature Name]

**Feature Branch**: `[###-feature-name]`
**Created**: [Date]
**Status**: Draft
**Input**: User description: "$ARGUMENTS"

## User Scenarios and Tests *(Required)*

<!--
  IMPORTANT: User stories must be written as prioritized user journeys in order of importance.
  Each user story/journey must be independently testable - meaning that implementing just one of them
  should result in a minimum viable product (MVP) that delivers value.

  Assign a priority (P1, P2, P3, etc.) to each story. P1 is most important.
  Think of each story as an independent slice of functionality that is:
  - Independently developable
  - Independently testable
  - Independently deployable
  - Independently demonstrable to users
-->

### User Story 1 - [Concise Title] (Priority: P1)

[Describe this user journey in plain language]

**Reason for this priority**: [Explain the value and why it has this priority level]

**Independent testing**: \[Explain how this can be tested independently - e.g., "Can be fully tested by [specific action] and provides [specific value]"\]

**Acceptance Scenarios**:

1. **Given** [initial state], **When** [action], **Then** [expected result]
1. **Given** [initial state], **When** [action], **Then** [expected result]

______________________________________________________________________

### User Story 2 - [Concise Title] (Priority: P2)

[Describe this user journey in plain language]

**Reason for this priority**: [Explain the value and why it has this priority level]

**Independent testing**: [Explain how this can be tested independently]

**Acceptance Scenarios**:

1. **Given** [initial state], **When** [action], **Then** [expected result]

______________________________________________________________________

### User Story 3 - [Concise Title] (Priority: P3)

[Describe this user journey in plain language]

**Reason for this priority**: [Explain the value and why it has this priority level]

**Independent testing**: [Explain how this can be tested independently]

**Acceptance Scenarios**:

1. **Given** [initial state], **When** [action], **Then** [expected result]

______________________________________________________________________

[Add additional user stories as needed, each with an assigned priority]

### Edge Cases

<!--
  ACTION REQUIRED: The contents of this section are placeholders.
  Fill in with appropriate edge cases.
-->

- What happens when [boundary condition]?
- How does the system handle [error scenario]?

## Requirements *(Required)*

<!--
  ACTION REQUIRED: The contents of this section are placeholders.
  Fill in with appropriate functional requirements.
-->

### Functional Requirements

- **FR-001**: The system must [specific function, e.g., "allow users to create accounts"]
- **FR-002**: The system must [specific function, e.g., "validate email addresses"]
- **FR-003**: Users must be able to [important interaction, e.g., "reset their passwords"]
- **FR-004**: The system must [data requirement, e.g., "persist user settings"]
- **FR-005**: The system must [behavior, e.g., "log all security events"]

*Example of marking unclear requirements:*

- **FR-006**: The system must authenticate users via [NEEDS CLARIFICATION: Authentication method not specified - email/password, SSO, OAuth?]
- **FR-007**: The system must retain user data for [NEEDS CLARIFICATION: Retention period not specified]

### Key Entities *(Include if the feature handles data)*

- **[Entity 1]**: [What it represents, key attributes without implementation details]
- **[Entity 2]**: [What it represents, relationships with other entities]

## Success Criteria *(Required)*

<!--
  ACTION REQUIRED: Define measurable success criteria.
  These must be technology-independent and measurable.
-->

### Measurable Outcomes

- **SC-001**: [Measurable metric, e.g., "Users can complete account creation within 2 minutes"]
- **SC-002**: [Measurable metric, e.g., "System can handle 1000 concurrent users without performance degradation"]
- **SC-003**: [User satisfaction metric, e.g., "90% of users successfully complete the main task on first attempt"]
- **SC-004**: \[Business metric, e.g., "Reduce support tickets related to [X] by 50%"\]

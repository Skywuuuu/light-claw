# Engineering Style Guardrails

## Core Principle
When modifying this codebase, always prefer solutions that keep the system **minimal, lightweight, and easy to understand**.

The default optimization direction is:

- fewer modules
- fewer dependencies
- fewer abstraction layers
- fewer files
- fewer moving parts

Do **not** optimize by making the code more “architected” unless there is a clear and immediate benefit.

## Priority Order
When making trade-offs, follow this order:

1. correctness
2. readability
3. simplicity
4. maintainability
5. performance

Performance improvements are welcome, but never at the cost of making the code noticeably harder to read or modify unless absolutely necessary.

## Default Coding Behavior
Prefer:

- direct and explicit code
- local, minimal changes
- standard library over third-party dependencies
- simple data flow over indirection
- code that a new contributor can understand quickly
- keeping logic close to where it is used
- small and practical refactors

Avoid:

- over-engineering
- premature abstraction
- unnecessary generalization
- splitting code into many tiny files or layers
- adding helper/wrapper/manager/service classes without strong justification
- introducing frameworks or dependencies for small problems
- redesigning architecture when a local fix is enough
- abstracting only for hypothetical future reuse

## Abstraction Policy
Abstraction is allowed only when it provides a **clear present-day benefit**, such as:

- removing truly confusing duplication
- isolating a genuinely reusable and stable pattern
- reducing bug-prone complexity
- clarifying responsibilities in an already large or messy area

Do not extract abstractions just to make the code look cleaner in theory.

A small amount of duplication is preferable to a hard-to-follow abstraction.

## Dependency Policy
Before introducing any new dependency, assume the answer is **no**.

Only add a dependency when it clearly provides substantial value that cannot be reasonably achieved with:

- the standard library
- existing project dependencies
- a small amount of straightforward local code

If a new dependency is introduced, explain why it is necessary.

## Refactoring Policy
Refactoring should be **incremental and conservative** by default.

Prefer:

- simplifying conditionals
- removing dead code
- inlining low-value wrappers
- collapsing unnecessary layers
- renaming for clarity
- reducing mental overhead

Do not perform large-scale rewrites unless explicitly requested or clearly required by the current task.

“Optimization” usually means **simplifying the current implementation**, not replacing it with a more elaborate system.

## File and Module Policy
Do not create new files or modules unless there is a real need.

Prefer keeping related logic together when that makes the code easier to navigate.

Do not split code purely for stylistic reasons.

## Communication Policy
When proposing or making non-trivial changes:

- explain the simplest acceptable approach first
- mention any more complex alternatives only if relevant
- explicitly state when complexity was intentionally avoided
- keep explanations concrete and practical

If multiple valid implementations exist, choose the one that is:

- easier to read
- easier to debug
- easier to modify
- more consistent with the existing codebase
- less abstract

## Anti-Patterns to Avoid
Unless explicitly requested, avoid introducing:

- dependency injection layers
- plugin systems
- generic factories
- deep inheritance hierarchies
- excessive configuration systems
- event buses for local logic
- “manager” or “service” objects with vague responsibilities
- utility layers that hide simple logic
- abstractions designed mainly for imagined future scale

## Rule of Thumb
If a solution feels clever, highly reusable, or architecturally impressive, pause and ask:

**Can this be solved in a more direct, smaller, and more obvious way?**

In this repository, simple and obvious is usually better than elegant-but-abstract.

## Change Expectations
For any meaningful code change, aim to:

1. identify the simplest change that solves the problem
2. preserve the existing structure unless there is a strong reason not to
3. avoid introducing new abstractions unless they clearly reduce current complexity
4. keep the final code easy to read without extra explanation

When summarizing changes, include:

- what was simplified
- what complexity was removed or avoided
- whether any trade-off was intentionally made in favor of simplicity
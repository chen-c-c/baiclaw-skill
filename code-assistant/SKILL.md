---
name: code-assistant
description: Expert code generation, review, refactoring, and debugging assistant. Use this skill for any programming task that requires multi-step reasoning, reading existing files, and producing high-quality code.
official: true
version: 1.0.0
---

# Code Assistant Skill

## When to Use This Skill

Use the code-assistant skill when you need to:

- **Generate code** - Write new functions, classes, modules, or complete files
- **Review code** - Analyse code quality, find bugs, suggest improvements
- **Refactor code** - Reorganise or optimise existing code without changing behaviour
- **Debug** - Diagnose errors, trace logic flows, explain stack traces
- **Explain code** - Break down complex code into understandable explanations
- **Write tests** - Generate unit or integration tests for existing code

**Examples of when to use:**
- User: "Write a Go HTTP handler that validates JWT tokens"
- User: "Review my Python script for security issues"
- User: "Refactor this function to be more readable"
- User: "Why is this TypeScript code throwing a type error?"
- User: "Write unit tests for the functions in utils.go"

## Workflow

1. If the user references an existing file, use `read_file` to load it first.
2. Understand the programming language, framework, and context.
3. Apply best practices for the target language (idiomatic code, error handling, security).
4. For multi-file tasks, use `list_dir` to discover the project structure.
5. Produce complete, working code — not pseudocode or placeholders.
6. If writing to a file is requested, use `write_file`.
7. Always explain key decisions and trade-offs in the implementation.

## Available Tools

### read_file — Load an existing source file
```json
{"tool": "read_file", "arguments": {"path": "path/to/file.go"}}
```

### list_dir — Explore project structure
```json
{"tool": "list_dir", "arguments": {"path": "."}}
```

### write_file — Save generated or modified code
```json
{"tool": "write_file", "arguments": {"path": "path/to/output.go", "content": "package main\n..."}}
```

## Code Quality Standards

- **Correctness**: Code must compile/run without modification.
- **Security**: Follow OWASP guidelines; never introduce SQL injection, path traversal, or XSS.
- **Readability**: Clear variable names, logical structure, minimal nesting.
- **Error handling**: All errors must be handled appropriately for the language.
- **Tests**: Generated tests must be self-contained and runnable.

## Output Format

For code generation tasks, structure the response as:

1. Brief explanation of the approach
2. The complete code in a fenced code block with the correct language tag
3. Usage example or how to run it
4. Any caveats or limitations

For code review tasks:
1. Overall assessment (Good / Needs improvement / Critical issues)
2. Specific findings with line references
3. Suggested fixes with corrected code snippets

package deploymint.no_root_user

# Dockerfile: must declare a non-root USER
deny contains msg if {
    input.kind == "dockerfile"
    not has_user_instruction
    msg := {
        "id": "DM_ROOT_USER",
        "severity": "critical",
        "message": "Dockerfile has no USER instruction; container will run as root (UID 0).",
        "remediation": "Add a non-root user: RUN useradd -r -u 10001 appuser  /  USER 10001",
    }
}

deny contains msg if {
    input.kind == "dockerfile"
    some line in input.lines
    lower_line := lower(trim_space(line))
    startswith(lower_line, "user ")
    user := trim_space(substring(lower_line, 5, -1))
    user in {"root", "0"}
    msg := {
        "id": "DM_ROOT_USER_EXPLICIT",
        "severity": "critical",
        "message": sprintf("Dockerfile explicitly sets USER to '%s'.", [user]),
        "remediation": "Use a non-root UID, e.g. USER 10001",
    }
}

has_user_instruction if {
    some line in input.lines
    startswith(lower(trim_space(line)), "user ")
}

# Kubernetes: must not run as root
deny contains msg if {
    input.kind == "Deployment"
    some c in input.spec.template.spec.containers
    not c.securityContext.runAsNonRoot
    not input.spec.template.spec.securityContext.runAsNonRoot
    msg := {
        "id": "DM_K8S_ROOT",
        "severity": "critical",
        "message": sprintf("Container '%s' does not set runAsNonRoot: true.", [c.name]),
        "remediation": "Set securityContext.runAsNonRoot: true and runAsUser: 10001",
    }
}

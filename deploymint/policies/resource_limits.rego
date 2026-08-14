package deploymint.resource_limits

deny contains msg if {
    input.kind == "Deployment"
    some c in input.spec.template.spec.containers
    not c.resources.limits.memory
    msg := {
        "id": "DM_NO_MEM_LIMIT",
        "severity": "high",
        "message": sprintf("Container '%s' has no memory limit; a leak can evict every pod on the node.", [c.name]),
        "remediation": "Set resources.limits.memory, e.g. \"512Mi\"",
    }
}

deny contains msg if {
    input.kind == "Deployment"
    some c in input.spec.template.spec.containers
    not c.resources.limits.cpu
    msg := {
        "id": "DM_NO_CPU_LIMIT",
        "severity": "high",
        "message": sprintf("Container '%s' has no CPU limit.", [c.name]),
        "remediation": "Set resources.limits.cpu, e.g. \"500m\"",
    }
}

deny contains msg if {
    input.kind == "Deployment"
    some c in input.spec.template.spec.containers
    not c.resources.requests
    msg := {
        "id": "DM_NO_REQUESTS",
        "severity": "medium",
        "message": sprintf("Container '%s' has no resource requests; the scheduler cannot place it well.", [c.name]),
        "remediation": "Set resources.requests.cpu and resources.requests.memory",
    }
}

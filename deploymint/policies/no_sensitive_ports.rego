package deploymint.no_sensitive_ports

sensitive := {
    22:    "SSH",
    23:    "Telnet",
    2375:  "Docker daemon (unencrypted)",
    2376:  "Docker daemon (TLS)",
    3306:  "MySQL",
    5432:  "PostgreSQL",
    6379:  "Redis",
    9200:  "Elasticsearch",
    27017: "MongoDB",
    11211: "Memcached",
}

deny contains msg if {
    input.kind == "dockerfile"
    some line in input.lines
    startswith(lower(trim_space(line)), "expose ")
    port := to_number(trim_space(substring(lower(trim_space(line)), 7, -1)))
    svc := sensitive[port]
    msg := {
        "id": "DM_SENSITIVE_PORT",
        "severity": "high",
        "message": sprintf("Dockerfile EXPOSEs port %d (%s), which should not be public.", [port, svc]),
        "remediation": "Remove the EXPOSE; reach backing services over the cluster network instead.",
    }
}

deny contains msg if {
    input.kind == "Service"
    input.spec.type in {"NodePort", "LoadBalancer"}
    some p in input.spec.ports
    svc := sensitive[p.port]
    msg := {
        "id": "DM_SENSITIVE_PORT_EXPOSED",
        "severity": "critical",
        "message": sprintf("Service of type %s exposes port %d (%s) outside the cluster.", [input.spec.type, p.port, svc]),
        "remediation": "Use ClusterIP for internal services.",
    }
}

"""Deterministic Dockerfile/K8s templates — the fallback that guarantees a run
always produces artifacts. See docs/06-phase-2-generation.md §2.5."""

from deploymint.schemas.artifacts import GeneratedArtifacts

PY_VERSION = "3.11"  # a safe, modern default — see the note in _python_fastapi()


def _entrypoint_module(entrypoint: str) -> str:
    """'main.py' -> 'main'; 'app/main.py' -> 'app.main'."""
    if not entrypoint:
        return "main"
    return entrypoint.removesuffix(".py").replace("/", ".")


def _labels(name: str) -> str:
    return f"app: {name}, managed-by: deploymint"


def _k8s_deployment(name: str, image: str, port: int) -> str:
    return f"""\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {name}
  labels: {{ {_labels(name)} }}
spec:
  replicas: 1
  selector:
    matchLabels: {{ app: {name} }}
  template:
    metadata:
      labels: {{ app: {name} }}
    spec:
      securityContext:
        runAsNonRoot: true
        runAsUser: 10001
        fsGroup: 10001
      containers:
        - name: {name}
          image: {image}
          imagePullPolicy: IfNotPresent
          ports:
            - containerPort: {port}
          resources:
            requests: {{ cpu: "100m", memory: "128Mi" }}
            limits:   {{ cpu: "500m", memory: "512Mi" }}
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            runAsNonRoot: true
            runAsUser: 10001
            capabilities: {{ drop: ["ALL"] }}
          volumeMounts:
            - name: tmp
              mountPath: /tmp
          livenessProbe:
            httpGet: {{ path: /health, port: {port} }}
            initialDelaySeconds: 15
            periodSeconds: 20
          readinessProbe:
            httpGet: {{ path: /health, port: {port} }}
            initialDelaySeconds: 5
            periodSeconds: 10
      volumes:
        - name: tmp
          emptyDir: {{}}
"""


def _k8s_service(name: str, port: int) -> str:
    return f"""\
apiVersion: v1
kind: Service
metadata:
  name: {name}-svc
  labels: {{ {_labels(name)} }}
spec:
  type: ClusterIP
  selector:
    app: {name}
  ports:
    - port: {port}
      targetPort: {port}
"""


def _python_fastapi(analysis: dict, name: str, image: str) -> GeneratedArtifacts:
    port = analysis.get("exposed_port", 8000)
    module = _entrypoint_module(analysis.get("entrypoint") or "main.py")
    # NOTE: analysis["python_version"] currently reflects the SCANNING host's
    # interpreter, not the target repo's declared version (Phase 1 doesn't parse
    # requires-python / runtime.txt yet). Templates deliberately pin a safe,
    # modern default instead of trusting that field — see docs/04-agents-spec.md.
    dockerfile = f"""\
# ---- builder ----
FROM python:{PY_VERSION}-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---- runtime ----
FROM python:{PY_VERSION}-slim
RUN groupadd -r appuser -g 10001 && \\
    useradd -r -u 10001 -g appuser -s /sbin/nologin appuser
WORKDIR /app
COPY --from=builder /install /usr/local
COPY --chown=appuser:appuser . .
USER 10001
EXPOSE {port}
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \\
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:{port}/health').status==200 else 1)"
CMD ["python", "-m", "uvicorn", "{module}:app", "--host", "0.0.0.0", "--port", "{port}"]
"""
    dockerignore = "__pycache__/\n*.pyc\n.venv/\nvenv/\n.git/\n.deploymint/\n"
    return GeneratedArtifacts(
        dockerfile=dockerfile,
        dockerignore=dockerignore,
        k8s_deployment=_k8s_deployment(name, image, port),
        k8s_service=_k8s_service(name, port),
        reasoning="Deterministic template: multi-stage build, non-root UID 10001, "
        "layer-cached dependency install, health-checked liveness/readiness probes.",
    )


def _python_flask(analysis: dict, name: str, image: str) -> GeneratedArtifacts:
    art = _python_fastapi(analysis, name, image)
    port = analysis.get("exposed_port", 5000)
    module = _entrypoint_module(analysis.get("entrypoint") or "app.py")
    art.dockerfile = art.dockerfile.replace(
        f'CMD ["python", "-m", "uvicorn", "{_entrypoint_module(analysis.get("entrypoint") or "main.py")}:app", "--host", "0.0.0.0", "--port", "{analysis.get("exposed_port", 8000)}"]',
        f'CMD ["python", "-m", "flask", "--app", "{module}", "run", "--host=0.0.0.0", "--port={port}"]',
    )
    return art


def _python_generic(analysis: dict, name: str, image: str) -> GeneratedArtifacts:
    """No recognized framework — run the entrypoint directly with plain python."""
    port = analysis.get("exposed_port", 8000)
    entrypoint = analysis.get("entrypoint") or "main.py"
    dockerfile = f"""\
FROM python:{PY_VERSION}-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:{PY_VERSION}-slim
RUN groupadd -r appuser -g 10001 && \\
    useradd -r -u 10001 -g appuser -s /sbin/nologin appuser
WORKDIR /app
COPY --from=builder /install /usr/local
COPY --chown=appuser:appuser . .
USER 10001
EXPOSE {port}
CMD ["python", "{entrypoint}"]
"""
    return GeneratedArtifacts(
        dockerfile=dockerfile,
        dockerignore="__pycache__/\n*.pyc\n.venv/\nvenv/\n.git/\n.deploymint/\n",
        k8s_deployment=_k8s_deployment(name, image, port),
        k8s_service=_k8s_service(name, port),
        reasoning="No recognized framework — running the detected entrypoint directly.",
    )


def _node_express(analysis: dict, name: str, image: str) -> GeneratedArtifacts:
    port = analysis.get("exposed_port", 3000)
    entrypoint = analysis.get("entrypoint") or "server.js"
    dockerfile = f"""\
FROM node:20-slim AS builder
WORKDIR /build
COPY package*.json ./
RUN npm ci --omit=dev

FROM node:20-slim
RUN groupadd -r appuser -g 10001 && useradd -r -u 10001 -g appuser appuser
WORKDIR /app
COPY --from=builder /build/node_modules ./node_modules
COPY --chown=appuser:appuser . .
USER 10001
EXPOSE {port}
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \\
  CMD node -e "require('http').get('http://localhost:{port}/health', r => process.exit(r.statusCode===200?0:1)).on('error',()=>process.exit(1))"
CMD ["node", "{entrypoint}"]
"""
    return GeneratedArtifacts(
        dockerfile=dockerfile,
        dockerignore="node_modules/\n.git/\n.deploymint/\n",
        k8s_deployment=_k8s_deployment(name, image, port),
        k8s_service=_k8s_service(name, port),
        reasoning="Deterministic Node/Express template: prod-only npm install, non-root user.",
    )


def _node_generic(analysis: dict, name: str, image: str) -> GeneratedArtifacts:
    return _node_express(analysis, name, image)


def _go_generic(analysis: dict, name: str, image: str) -> GeneratedArtifacts:
    port = analysis.get("exposed_port", 8080)
    entrypoint = analysis.get("entrypoint") or "main.go"
    dockerfile = f"""\
FROM golang:1.22-alpine AS builder
WORKDIR /build
COPY go.mod go.sum* ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -o /app-bin ./{entrypoint.rsplit("/", 1)[0] if "/" in entrypoint else "."}

FROM alpine:3.19
RUN addgroup -g 10001 appuser && adduser -D -u 10001 -G appuser appuser
COPY --from=builder /app-bin /app-bin
USER 10001
EXPOSE {port}
CMD ["/app-bin"]
"""
    return GeneratedArtifacts(
        dockerfile=dockerfile,
        dockerignore=".git/\n.deploymint/\n",
        k8s_deployment=_k8s_deployment(name, image, port),
        k8s_service=_k8s_service(name, port),
        reasoning="Deterministic Go template: multi-stage static build, distroless-ish alpine runtime.",
    )


def _java_generic(analysis: dict, name: str, image: str) -> GeneratedArtifacts:
    port = analysis.get("exposed_port", 8080)
    dockerfile = f"""\
FROM eclipse-temurin:21-jdk AS builder
WORKDIR /build
COPY . .
RUN ./mvnw -q -DskipTests package || ./gradlew -q build -x test

FROM eclipse-temurin:21-jre
RUN groupadd -r appuser -g 10001 && useradd -r -u 10001 -g appuser appuser
WORKDIR /app
COPY --from=builder /build/target/*.jar app.jar
USER 10001
EXPOSE {port}
CMD ["java", "-jar", "app.jar"]
"""
    return GeneratedArtifacts(
        dockerfile=dockerfile,
        dockerignore="target/\nbuild/\n.git/\n.deploymint/\n",
        k8s_deployment=_k8s_deployment(name, image, port),
        k8s_service=_k8s_service(name, port),
        reasoning="Deterministic Java template: multi-stage Maven/Gradle build.",
    )


def _generic(analysis: dict, name: str, image: str) -> GeneratedArtifacts:
    return _python_generic(analysis, name, image)


REGISTRY = {
    ("python", "fastapi"): _python_fastapi,
    ("python", "flask"): _python_flask,
    ("python", "django"): _python_fastapi,  # gunicorn swap is a template refinement; safe default for now
    ("python", "*"): _python_generic,
    ("javascript", "express"): _node_express,
    ("javascript", "*"): _node_generic,
    ("go", "*"): _go_generic,
    ("java", "*"): _java_generic,
}


def render(analysis: dict, project_name: str, image: str) -> GeneratedArtifacts:
    key = (analysis.get("language"), analysis.get("framework"))
    fn = REGISTRY.get(key) or REGISTRY.get((analysis.get("language"), "*")) or _generic
    return fn(analysis, project_name, image)

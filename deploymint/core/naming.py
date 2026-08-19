"""Turns a user-typed project name into a safe identifier for everything
downstream that treats it as one: Docker image tags, Kubernetes resource
names, and Terraform resource identifiers.

Used to live only inside schemas/project.py's pydantic validator — which
meant it only ever ran on the JSON API path (POST /api/projects). The web
UI's own registration form (web/routes.py's register_project_form) built a
Project straight from the raw form field, bypassing it entirely. A name
like "bew proj" (spaces are the obvious way to hit this, but any
uppercase/punctuation would too) sailed through into the database, then into
`deploymint/{name}:{run_id}` as a Docker image tag — which Docker rejects
outright ("invalid reference format"), failing the run at the Execution
stage after every earlier stage had already succeeded. Found by actually
registering a project through the web form with a space in the name and
watching it fail, not by code review. See docs/22-naming.md.
"""


def slugify(name: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in "-_" else "-" for c in name.lower())
    if not cleaned.strip("-"):
        raise ValueError("name must contain at least one alphanumeric character")
    return cleaned

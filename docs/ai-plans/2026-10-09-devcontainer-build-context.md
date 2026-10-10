# Dev Container Build Context Implementation Plan

> **For agentic workers:** Execute this plan inline, task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the Dev Containers image build failure on recent Buildx without granting broad host filesystem access.

**Architecture:** Dev Containers writes `Dockerfile-with-features` below `${TMPDIR:-/tmp}/devcontainercli-${USER}/container-features`, outside the Python service's build context. Recent Buildx rejects that read unless Compose's Bake invocation passes a matching global `fs.read` allowance. Compose automatically grants read access to paths used by `build.additional_contexts`, so declare only the Dev Containers feature directory as a named additional context on `python`.

**Tech Stack:** Docker Compose, Docker Buildx, VS Code Dev Containers.

## Global Constraints

- `pyright strict 0 erros` — never rebaixar config.
- `domain/` é puro (sem I/O); `infra` implementa `app/ports.py`.
- Nenhum segredo em arquivo rastreado; `.env` nunca commita.
- Preserve pre-existing worktree edits; never grant filesystem access to all of `/tmp` or `*`.

---

### Task 1: Add the Dev Containers feature directory as a read context

**Files:**
- Modify: `.devcontainer/docker-compose.yml` — add a named additional context only to `python.build`.
- Modify: `.devcontainer/readme.md` — document why the narrow context is required.

**Interfaces:**
- Consumes: `${TMPDIR:-/tmp}/devcontainercli-${USER}/container-features`, created by the Dev Containers CLI before it invokes Compose build.
- Produces: Compose/Bake passes Buildx a read allowance for that directory; the Dockerfile does not need to consume the named context.

- [x] **Step 1: Confirm the failing behavior**

Run the Dev Containers CLI with `.devcontainer/devcontainer.json` and `--skip-post-create`.
Expected before the change: image build stops before Dockerfile execution with `additional privileges requested: pass "--allow=fs.read=/tmp/devcontainercli-<user>/container-features/<version>-<timestamp>/Dockerfile-with-features"`.

- [x] **Step 2: Add the minimal additional context**

Add this under `services.python.build` in `.devcontainer/docker-compose.yml`:

```yaml
      additional_contexts:
        devcontainer_features: ${TMPDIR:-/tmp}/devcontainercli-${USER}/container-features
```

Do not add it to `worker`, do not use a filesystem entitlement, and do not allow all of `/tmp` or `*`.

- [x] **Step 3: Document the behavior**

Explain in `.devcontainer/readme.md` that Dev Containers puts a generated feature Dockerfile outside the normal context and the named context lets Compose pass Buildx's required narrow read allowance.

- [x] **Step 4: Verify Compose configuration**

Run `docker compose -f .devcontainer/docker-compose.yml config --quiet`, then confirm `config --format json` reports the named context only for `python`.

- [x] **Step 5: Verify the Dev Container build**

Run the Dev Containers CLI for `.devcontainer/devcontainer.json` with `--skip-post-create`.
Expected: the Python image build completes without an `additional privileges requested` error.

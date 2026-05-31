# Pinned versions

For reproducibility, every upstream dependency that this testbed clones or pulls is pinned to a specific commit hash or image digest. The values below were captured on **2026-05-18** and verified to build the matrix described in the paper.

## Container base images

| Image                          | Reference                                                              |
|--------------------------------|------------------------------------------------------------------------|
| `ubuntu:24.04`                 | tracked by Canonical security updates; tag is stable                   |
| `mysql:5.7`                    | `sha256:4bc6bc963e6d8443453676cae56536f4b8156d78bae03c0145cbe47c2aad73bb` |
| `scadalts/scadalts:latest`     | tracked by SCADA-LTS upstream; lab snapshot will be added on freeze    |
| `tomcat:9-jdk11-temurin`       | tracked by Adoptium                                                    |

## Source-from-git pins

| Project                | URL                                                  | Commit                                       |
|------------------------|------------------------------------------------------|----------------------------------------------|
| OpenPLC_v3 runtime     | https://github.com/thiagoralves/OpenPLC_v3.git       | `b5d41356dab4aeadca0dd7ca64ba542f870b595d`   |

The OpenPLC commit is overridable at build time via `--build-arg OPENPLC_COMMIT=<hash>`; the default in `containers/plc/Dockerfile` is the pinned hash above.

## Python dependency versions

Listed in `pyproject.toml` (top-level) and `containers/{monitor,attacker}/requirements.txt` (per-container). All pinned to specific minor or patch versions.

## How to refresh pins for a new release

```bash
# OpenPLC: get the current master HEAD commit hash
git ls-remote https://github.com/thiagoralves/OpenPLC_v3.git refs/heads/master

# MySQL: get the current 5.7 digest
docker pull mysql:5.7
docker inspect mysql:5.7 --format '{{index .RepoDigests 0}}'

# SCADA-LTS: get the current latest digest
docker pull scadalts/scadalts:latest
docker inspect scadalts/scadalts:latest --format '{{index .RepoDigests 0}}'
```

Update the rows above with new values and commit, so that the experimental matrix can be reproduced bit-for-bit on a future machine.

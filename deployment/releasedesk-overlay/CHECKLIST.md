# After this config is approved — do these in order

Nothing in this list is done yet. This package is prep only.

StaffLess AI is the engine ReleaseDesk Everywhere talks to. Source tree:
**[Stafless-ai](https://github.com/ReleasedeskAU/Stafless-ai)** (our MIT-only
fork of [onyx-foss](https://github.com/onyx-dot-app/onyx-foss)). Images,
compose project, APIs, and `ONYX_*` names stay as that software ships them —
do not rename those. Do **not** clone or compose from the main `onyx` repo,
and do **not** clone bare `onyx-foss` (that tree has no ReleaseDesk overlay).

**Images:** foss does not publish `onyxdotapp/onyx-foss-backend` (or similar).
Hub `onyxdotapp/onyx-backend` is the **main** repo and includes `ee/`. Build
the backend from foss source (`--target runtime`) before `compose up`. See
README.md “Image source”.

Host OS is **Ubuntu Server 24.04 LTS**. Complete step 2 before the image
build, compose, or `setup-onyx.sh`. 24.04 is safe with Docker CE +
iptables-nft; it is **not** safe if you follow old “switch to
iptables-legacy” or native-`nft` guides.

1. **Review this package**
   - Confirm **D4as_v5** (4 vCPU / 16 GB; same budget as D4s_v5) and the RAM
     caps in `env.onyx.d4s-v5.example`.
   - Fill `REPLACE_ME_*` placeholders locally; never commit filled secrets.
   - Confirm Community/MIT path: unrestricted PAT, not `POST /admin/api-key`.
   - Confirm `ONYX_BACKEND_IMAGE=releasedesk/staffless-backend:local` and
     `LICENSE_ENFORCEMENT_ENABLED=false` (foss-built image only).

2. **Prepare the Ubuntu 24.04 host** (required extra step — see `HOST-SETUP.md`)
   - Install Docker Engine from Docker’s apt repo (`docker-ce` + Compose v2
     plugin). Do **not** use Ubuntu’s `docker.io` / `docker-compose` packages.
   - Confirm `iptables -V` shows `(nf_tables)`. Do **not** switch to
     `iptables-legacy`. Do **not** write firewall rules with `nft` or
     `flush ruleset`. Do **not** install `iptables-persistent`.
   - Leave UFW off unless you need it; Azure NSG is the inbound perimeter.
     If you enable UFW afterward, `systemctl restart docker`.
   - `sudo apt install --yes curl jq git` (same package names as 22.04).
   - Persist `vm.max_map_count=262144` (`/etc/sysctl.d/99-opensearch.conf`)
     or OpenSearch will fail mmap checks.
   - `docker run --rm hello-world` and `docker compose version` succeed.
   - Confirm **≥20 GB** free disk for the first backend image build.

3. **Clone this fork and build the backend image** (required — Hub is not foss)
   - `git clone https://github.com/ReleasedeskAU/Stafless-ai.git /opt/staffless-ai`
   - Copy `deployment/releasedesk-overlay/env.onyx.d4s-v5.example` →
     `/opt/staffless-ai/deployment/docker_compose/.env` and fill secrets.
     Do not set `IMAGE_TAG=latest` and skip the build.
   - From that compose directory:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.resources.yml \
  -f ../releasedesk-overlay/docker-compose.releasedesk.yml \
  build api_server background
```

   - Stock foss compose already has `build.context: ../../backend` and
     `target: runtime`. Do not add `build:` to the overlay (wrong relative
     path). Do not use `docker bake` (Hub cache-from + default `dev` stage).
   - First build: expect 30–60 minutes on D4as_v5 (hashed pip + Playwright
     Chromium). `COPY ./ee` copies foss’s stub `backend/ee/__init__.py`;
     leftover `requirements/ee.txt` still installs and inflates the image
     but should not fail the build.

4. **Start StaffLess AI standalone on the VM**
   - From `/opt/staffless-ai/deployment/docker_compose`, start with
     `docker-compose.yml` + `docker-compose.resources.yml` +
     `../releasedesk-overlay/docker-compose.releasedesk.yml`. Overlay last.
   - Confirm these containers are up: `api_server`, `background`, `relational_db`, `cache`, `opensearch`, `nginx`.
   - Confirm these are **not** running: `web_server`, `inference_model_server`, `indexing_model_server`, `minio`, Vespa.
   - Optional reranker: `pull reranker` then `up -d --no-deps --wait --wait-timeout 600 reranker` only (5g / 2 CPU; 3g / 1 CPU sat the cgroup). Internal `http://reranker:80`. Query+passages stay on the VM; weights come from Hugging Face Hub. Does not recreate other containers. Do not `build reranker`.
   - `GET /health` through nginx (port 3000 or 80) returns OK.
   - `GET /docs` works only while `ENABLE_PUBLIC_DOCS=true`.

5. **Run first-time setup once**
   - `../releasedesk-overlay/setup-onyx.sh` with `ONYX_SERVER_URL`, admin email/password, `OPENAI_API_KEY`.
   - Store the printed PAT in ReleaseDesk Everywhere secrets. It is shown once.
   - Set `ENABLE_PUBLIC_DOCS=false` and recreate `api_server` with
     `../releasedesk-overlay/recreate-api-server.sh` (one command). Nginx
     re-resolves the new IP; do not rely on a separate `nginx -s reload`.

6. **Prove StaffLess AI works on its own**
   - `Authorization: Bearer <pat>` against `POST /api/chat/create-chat-session` and `POST /api/chat/send-chat-message`.
   - Optional: one public web connector via the Community APIs (`POST /api/manage/credential`, connector + CC pair) and wait for an index run.
   - Do **not** wire ReleaseDesk Everywhere or Neo4j yet.

7. **Build the Neo4j-writer bolt-on**
   - HTTP service that accepts StaffLess AI Document Push payloads.
   - Maps Item / Person / Group the same way connector-engine `planNeo4jWrites` does.
   - Own auth (shared secret). No engine source changes required for the env hook.

8. **Connect Document Push**
   - Set `DOCUMENT_PUSH_ENDPOINT_URL` (and optional `DOCUMENT_PUSH_API_KEY`) in `.env`.
   - Recreate `background` (and `api_server` if it also pushes).
   - Index one document; confirm a Neo4j write. Failures must not fail StaffLess AI indexing.

9. **Fix ReleaseDesk Everywhere connector-admin API calls**
   - Use the audited routes (not the design-doc guesses): credential then `PUT /api/manage/connector/{id}/credential/{id}`; indexing-status is **POST**; LLM is `/api/admin/llm/provider`; auth is the PAT from step 5, not a Business service key.
   - Do not send `retrieval_options.real_time` — that field is gone. Jira/GitHub stay indexed connectors; federated search is Slack-only today.

10. **End-to-end test**
   - ReleaseDesk Everywhere creates/updates a connector through StaffLess AI.
   - Index run completes in OpenSearch.
   - Document Push lands in Neo4j.
   - ReleaseDesk Everywhere chat/search returns indexed results.
   - Confirm StaffLess AI's own UI is still not in the browser path.

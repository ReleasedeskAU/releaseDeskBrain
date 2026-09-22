# StaffLess AI deployment prep (ReleaseDesk Everywhere)

StaffLess AI is ReleaseDesk Everywhere's search/RAG engine. This folder is
the ReleaseDesk overlay in our [Stafless-ai](https://github.com/ReleasedeskAU/Stafless-ai)
fork of [onyx-foss](https://github.com/onyx-dot-app/onyx-foss) (MIT-only daily
mirror). Image names, the compose project, API paths, and every `ONYX_*`
variable stay exactly as that software ships them.

Configuration only. Do not deploy until this package is reviewed.

Host OS: **Ubuntu Server 24.04 LTS (noble)**. VM size: **Azure D4as_v5**
(4 vCPU / 16 GB, AMD). Same RAM/CPU budget as D4s_v5 — compose limits are
unchanged. Containers do not care about 22.04 vs 24.04; the host firewall
backend and Docker install do. Complete `HOST-SETUP.md` **before** the image
build or compose.

This overlay sits next to stock compose at `deployment/docker_compose/`.
Always clone **this fork** on the VM (not a fresh `onyx-foss` checkout) so
the overlay and StaffLess AI branding stay with the tree.

---

## Image source — local foss build, not Docker Hub

`onyx-foss` does **not** publish its own registry images. There is no
`onyxdotapp/onyx-foss-backend`. Compose and `docker-bake.hcl` in that repo
still name `onyxdotapp/onyx-backend` — those Hub tags are built from the
**main** `onyx` repo and include `backend/ee`.

To run MIT-only StaffLess AI, clone **this fork** on the VM and **build the
backend image from that tree** (`--target runtime`). The overlay does not
start `web_server` or local model servers, so only the backend image is
required. First build on D4as_v5: hashed pip of `requirements/default.txt` +
leftover `requirements/ee.txt`, then Playwright Chromium — expect **30–60
minutes** (not timed on this SKU). Keep **≥20 GB** free disk for layers.

Do **not** use `docker bake` for this deploy: bake `cache-from` pulls Hub
`onyxdotapp/onyx-backend` (main/EE) and the bake target does not set
`--target runtime` (Dockerfile default last stage is `dev`).

```bash
# on the VM after HOST-SETUP.md
git clone https://github.com/ReleasedeskAU/Stafless-ai.git /opt/staffless-ai
cd /opt/staffless-ai/deployment/docker_compose
cp ../releasedesk-overlay/env.onyx.d4s-v5.example .env
# fill REPLACE_ME; ONYX_BACKEND_IMAGE=releasedesk/staffless-backend:local is already set

docker compose \
  -f docker-compose.yml \
  -f docker-compose.resources.yml \
  -f ../releasedesk-overlay/docker-compose.releasedesk.yml \
  build api_server background

docker compose \
  -f docker-compose.yml \
  -f docker-compose.resources.yml \
  -f ../releasedesk-overlay/docker-compose.releasedesk.yml \
  up -d --wait
```

### Optional CPU reranker (TEI)

Hugging Face Text Embeddings Inference serves `BAAI/bge-reranker-base` as
`http://reranker:80` on the compose network. **No host port.** Query text
and candidate passages stay on this VM; weights download from Hugging Face
Hub on first start into volume `reranker_model_cache` (~1 GB plus the TEI
CPU image). Cgroup cap is **5g / 2 CPU** (3g / 1 CPU sat the cgroup;
30-candidate rerank was ~7.5s). Ask/search
is **not** pointed at it (that would recreate `api_server`).

Embeddings use the overlay's one shared `inference_model_server`
(SentenceTransformer, `Qwen/Qwen3-Embedding-0.6B`). Do **not** start
`indexing_model_server` (second 5g cgroup). Do **not** embed through TEI.

Do **not** start a second encoder for the reranker. Pull then start **only**
`reranker`:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.resources.yml \
  -f ../releasedesk-overlay/docker-compose.releasedesk.yml \
  pull reranker

docker compose \
  -f docker-compose.yml \
  -f docker-compose.resources.yml \
  -f ../releasedesk-overlay/docker-compose.releasedesk.yml \
  up -d --no-deps --wait --wait-timeout 600 reranker
```

Do not `build reranker` (pulled image). Do not unscoped `up --force-recreate`.

Equivalent without compose (same Dockerfile target):

```bash
docker build --target runtime -t releasedesk/staffless-backend:local \
  /opt/staffless-ai/backend
```

### Leftover `ee` in foss (build vs runtime)

| Item | Risk |
| --- | --- |
| `COPY ./ee` + `requirements/ee.txt` in `backend/Dockerfile` | Build should **not** fail. Foss keeps stub `backend/ee/__init__.py` and the leftover hashed `ee.txt`. Image is larger than a true MIT-only dep set. |
| Dockerfile LABEL / `seed_dev_license.py` | Leftover text/script. Unused at runtime. |
| `LICENSE_ENFORCEMENT_ENABLED` default `true` | Sets `is_ee_version()` even with no EE package. `fetch_versioned_implementation` falls back. Leftover hard import `ee.onyx.utils.tier` in `PATCH /admin/settings` would crash. Env example sets the flag **false**. |
| MIT `/admin/api-key` still mounted | License middleware lived in `ee/`. Do not mint that key; use `POST /user/pats`. |
| `web/src/ee` leftover pages | Unused while overlay keeps `web_server` off. |
| Alembic `license` table in MIT migrations | Empty table; not a compose failure. |
| foss `.github/workflows/deployment.yml` | Copied from main; still tags `onyxdotapp/onyx-*`. Foss mirror does not publish a separate Hub repo. |

---

## Question 1 — First-time setup without StaffLess AI's UI

StaffLess AI auth is always on (`AUTH_TYPE` defaults to `basic`). There is no
`AUTH_TYPE=disabled`. Admin APIs require a logged-in first user.

### Required sequence (Community / MIT)

Confirmed in `backend/onyx/auth/users.py` (first registered user becomes
admin), `terraform-provider-onyx/examples/bootstrap/mint_api_key.sh`, and the
FastAPI routers below. Nginx in stock compose **strips `/api`** and sends
`/auth/*` (except SAML) to the **web** container. This package's nginx overlay
sends `/auth` and `/` to the API so the UI container is not required.

| Step | Method | Path on the API (after nginx `/api` rewrite) | Auth |
| --- | --- | --- | --- |
| 1. Create first admin | `POST` | `/auth/register` body `{email, username, password}` — `username` must equal `email` | none |
| 2. Session cookie | `POST` | `/auth/login` form `username` + `password` | none |
| 3. LLM provider | `PUT` | `/admin/llm/provider?is_creation=true` (`LLMProviderUpsertRequest`) | admin cookie |
| 4. Default chat model | `POST` | `/admin/llm/default` `{provider_id, model_name}` | admin cookie |
| 5. Cloud embeddings | `PUT` | `/admin/embedding/embedding-provider` `{provider_type, api_key}` | admin cookie |
| 6. Switch index off local nomic | `POST` | `/search-settings/set-new-search-settings` | admin cookie |
| 7. ReleaseDesk Everywhere bearer token | `POST` | `/user/pats` `{name, expiration_days, scopes}` | admin cookie |

`scopes: null` (omit or JSON `null`) = **full user access**, including
connector admin. That is the Community-safe substitute for a service API key.

Re-running step 1 on an existing email returns **400**; login is then the gate
(`mint_api_key.sh` treats 2xx and 400 as OK).

### What already automates this

- **Official, but Enterprise-coupled:**
  `terraform-provider-onyx/examples/bootstrap/mint_api_key.sh`
  then Terraform `onyx_llm_provider`. The shell script lists groups via
  `GET /manage/admin/user-group?include_default=true` and mints
  `POST /admin/api-key`. On Hub/main images both routes are **Business-tier**
  (`PATH_PREFIX_MIN_TIER` in `backend/ee/onyx/configs/license_enforcement_config.py`)
  and return **402** without a paid license. Foss does not ship that EE file;
  the MIT `/admin/api-key` router is still mounted — do not use it. Terraform's
  own README says listing groups "needs Enterprise Edition, same as the admin panel."
- **Community LLM/embeddings:** Terraform resources work **after** you already
  have a key. They do not register the first user.
- **Swagger:** `/docs` is **not registered** unless `ENABLE_PUBLIC_DOCS=true`
  (`env.template`). Stock nginx also does not proxy `/docs` to the API.

There is **no** Community first-boot script that does register → LLM →
embeddings → PAT. `setup-onyx.sh` in this folder is that sequence.

### Enterprise vs Community (do not bypass license)

| Capability | Edition |
| --- | --- |
| Register, login, LLM provider, embeddings, search settings, connectors, chat | Community (MIT) |
| `POST /user/pats` unrestricted PAT | Community |
| `POST /admin/api-key` service accounts | **Business+** |
| `GET /manage/admin/user-group` | **Business+** |
| `DOCUMENT_PUSH_ENDPOINT_URL` env push | Community (`document_push.py`) |
| Admin UI Document Push hook `/admin/hooks` | **Enterprise** |
| `LICENSE_ENFORCEMENT_ENABLED=false` | **Hub/main images:** unlocks paid routes without a license — do not set. **foss-built images:** set `false` in `env.onyx.d4s-v5.example` so leftover MIT code does not treat the process as EE and hard-import missing `ee.onyx.*`. |

`LICENSE_ENFORCEMENT_ENABLED` **defaults true**. On Hub images, EE code loads
and paid features stay locked. On foss, `backend/ee` is a stub: `set_ee()`
still runs if the default is left true, then `fetch_versioned_implementation`
falls back to MIT — except a few leftover hard imports. Keep
`ENABLE_PAID_ENTERPRISE_EDITION_FEATURES=false` and
`LICENSE_ENFORCEMENT_ENABLED=false` on the foss image only.

---

## Question 2 — Real-time search (original source)

This is **not** on by default, and it is **not** a chat request flag.

The sample script `backend/scripts/api_inference_sample.py` still sends
`retrieval_options.real_time: true`. Current `SendMessageRequest`
(`backend/onyx/server/query_and_chat/models.py`) has **no** `real_time` field
and **no** `retrieval_options`. Extra JSON is ignored; that sample is stale.

The real mechanism is **federated connectors**: query-time search of the live
source, attached to a **document set**, not to each chat call.

| Name | Where |
| --- | --- |
| Create federated connector | `POST /federated` (`FederatedConnectorRequest`: `source`, `credentials`, `config`) |
| Attach to a document set | `federated_connectors: [{federated_connector_id, entities}]` on `POST /manage/admin/document-set` |
| Registry today | **only** `federated_slack` (`federated_connectors/registry.py`) |

Jira / GitHub ReleaseDesk Everywhere connectors are **indexed** (poll or webhook →
OpenSearch). They are not federated. "Bypass indexing delay" for those sources
is connector refresh / webhooks, not a search API parameter.

Chat still uses the `run_search` tool against the index unless the persona's
document set includes a federated connector.

---

## How to use these files (still prep — not deploy)

1. On a fresh **Ubuntu 24.04** VM, follow `HOST-SETUP.md` (Docker CE from Docker’s
   apt repo, keep **iptables-nft**, raise `vm.max_map_count`, install `curl`/`jq`/`git`).
   Do not switch to `iptables-legacy` and do not manage the firewall with `nft`.
2. Clone **this fork** (not the main `onyx` repo and not a bare `onyx-foss`
   tree). From `deployment/docker_compose`, copy the env from
   `../releasedesk-overlay/`, **build**, then `up` (commands in **Image
   source** above). Then run `../releasedesk-overlay/setup-onyx.sh` once. See
   `CHECKLIST.md` for everything after that.

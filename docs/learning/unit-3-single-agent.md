# Unit 3: Single-Agent Code Review

Unit 3 replaces the worker's simulated review with a LangGraph agent that reviews the diff with a local LLM. This file grows as the unit is built, and it only describes what exists so far.

**Files so far:** `docker-compose.yml`, `tests/test_ollama.py`, `pytest.ini`

## Reaching Ollama from the containers

Ollama runs on the host, not in Compose, so it can use the machine's memory directly and its models stay put when containers are rebuilt. The app reaches it at `OLLAMA_BASE_URL`, which defaults to `http://host.docker.internal:11434`. Two things had to change before that address worked from inside a container.

```text
api / worker container ──host.docker.internal──▶ 172.17.0.1 (host, docker0) ──▶ Ollama on *:11434
```

### 1. The name: `extra_hosts`

```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```

**Docker Desktop**, on macOS and Windows, defines `host.docker.internal` in every container. **Docker on Linux doesn't.** Without this setting, the name doesn't resolve: the first run of the tests failed with `Temporary failure in name resolution`, and each failure took several seconds while the DNS lookup timed out.

`host-gateway` is a special value that Docker replaces with the host's address on its default bridge network. Here that's `172.17.0.1`, the `docker0` interface. Compose writes the mapping into the container's `/etc/hosts`, the same file that maps names on any Linux machine. `getent hosts host.docker.internal` inside the container shows it.

Both `api` and `worker` have the setting. The worker is what will call the model, and the API gets it so the checks can run from `docker compose exec api pytest` like every other test.

### 2. The address Ollama listens on: `OLLAMA_HOST`

Ollama listened on `127.0.0.1:11434` by default. That's the host's loopback, which only accepts connections from the host itself. A container's request arrives at `172.17.0.1`, a different address, so it would be refused even once the name resolved.

The fix is outside the repository, in a systemd drop-in for the Ollama service, `/etc/systemd/system/ollama.service.d/override.conf`:

```ini
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
```

A drop-in is a small file that adds to or overrides a unit without editing the unit file itself, so a package upgrade that replaces the unit keeps the change. `sudo systemctl edit ollama` creates one through an editor. Writing the file directly works too, but then `sudo systemctl daemon-reload` is needed so systemd notices it. `sudo systemctl restart ollama` applies it, and `ss -ltn | grep 11434` shows the result: `*:11434`, all interfaces.

Laravel Sail has the same problem with Xdebug calling back to the host, and solves it with the same `extra_hosts` line.

**This opens Ollama to the local network.** `0.0.0.0` means every interface, and the host's firewall (`ufw`) is inactive, so any device on the same network can use the Ollama API. The API has no authentication, and it can pull and delete models as well as run them. See "Known limitations".

## The model: `llama3.2`

`LLM_MODEL` is `llama3.2`, which Ollama resolves to `llama3.2:latest`, the 3-billion-parameter version, about 2 GB. The machine has no GPU that Ollama uses, so the model runs on the CPU.

Measured from inside the worker container on 2026-11-02, with a short SQL injection snippet as the prompt and a 300-token limit:

| Model | Generation speed | Notes |
|---|---|---|
| `llama3.2:1b` | about 9 tokens/s (from the host) | Fast, but small models are weak at structured output |
| `llama3.2` (3B) | about 6 tokens/s | 300 tokens in about 54 seconds, found the SQL injection |
| `mistral` (7B) | about 2.4 tokens/s (from the host) | Several minutes per review |

A token is roughly three quarters of a word. About a minute per review is slow for a person waiting, but the worker runs in the background, and the unit tests use a mocked LLM. How good the reviews are is a question for step 3.9, which runs real reviews.

## Tests: `tests/test_ollama.py`

Four checks, all from inside the `api` container:

- `host.docker.internal` resolves.
- `GET /api/version` answers.
- The configured model is pulled. `GET /api/tags` lists the models, and Ollama adds `:latest` to a name pulled without a tag, so the test does the same.
- A short prompt gets a non-empty reply through `POST /api/chat`. `num_predict: 10` limits the reply to 10 tokens and `temperature: 0` makes it as repeatable as the model allows. The test takes about 5 seconds, mostly loading the model.

They call Ollama's HTTP API with `httpx` directly. The LangChain integration comes in step 3.2, and these checks are about the connection, not the library.

**The `ollama` marker.** The file sets `pytestmark = pytest.mark.ollama`, which marks every test in it. The marker is registered in `pytest.ini`, because pytest warns about unknown markers. `pytest -m "not ollama"` skips them. The CI pipeline in Unit 6 will need that, because it won't have a local model. Locally they run with everything else.

## Known limitations

- **Ollama is reachable from the local network.** It listens on all interfaces, the firewall is inactive, and Ollama's API has no authentication. Anyone on the same network can run, pull, or delete models. The fix is a firewall rule that allows only Docker's networks to reach port 11434. It isn't applied yet.
- **The listen address lives outside the repository.** A fresh machine needs the systemd drop-in by hand. These notes are the only record of it.
- **`host-gateway` points at `docker0`.** The Compose network has its own bridge (`172.25.0.1` here), but traffic to `172.17.0.1` still reaches the host, because both are the host's own addresses. That was verified by the tests passing, not traced through the routing.
- **Speed was measured once, with one prompt.** Real diffs are longer, and the prompt part grows with them.

## Verification record

Step 3.1, on 2026-11-02: the four tests in `tests/test_ollama.py` failed first with `Temporary failure in name resolution`. After adding `extra_hosts` and recreating `api` and `worker`, both containers resolved `host.docker.internal` to `172.17.0.1`, and the four tests passed in 5.3 seconds. A deliberate break, `LLM_MODEL=not-a-pulled-model`, failed the two model tests and left the two connection tests passing. From the worker container, `llama3.2` answered a 49-token review prompt with 300 tokens at 6.0 tokens/s, 54.1 seconds in total, and named the SQL injection. Full suite: 155 passed, and 151 passed with `-m "not ollama"`.

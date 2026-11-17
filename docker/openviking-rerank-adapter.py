#!/usr/bin/env python3
"""OpenAI-compatible rerank shim backed by Volcengine Knowledge Base rerank.

Why this exists
---------------
Volcengine retired the VikingDB vector-database rerank endpoint
(``/api/vikingdb/rerank``): every call now answers
``400 Rerank is temporarily unavailable in VikingDB. Please use Knowledge Base``.
The live service is the Knowledge Base rerank API
(``POST https://api-knowledgebase.mlp.cn-beijing.volces.com/api/knowledge/service/rerank``),
which needs AK/SK signing with ``service="air"`` and a different request/response
shape. OpenViking's built-in ``provider: "vikingdb"`` still speaks the retired
protocol (see volcengine/OpenViking issue #5060, closed unfixed), so this shim
speaks OpenAI's rerank protocol on the outside and the Knowledge Base protocol
on the inside; ``provider: "openai"`` in ``ov.conf`` then points at it.

Endpoints
---------
GET  /healthz        -> {"status": "ok"}
POST /v1/reranks     -> {"model", "query", "documents": [...], "top_n"?}
                     <- {"results": [{"index": i, "relevance_score": s}], "usage": {...}}

Credentials
-----------
Read from ``KB_RERANK_CREDS`` (default ``/etc/openviking/rerank-kb.json``),
a JSON file ``{"ak": "...", "sk": "..."}`` kept outside git, mode 600.
Environment overrides: KB_RERANK_HOST, KB_RERANK_PATH, KB_RERANK_SERVICE,
KB_RERANK_REGION, KB_RERANK_MODEL, KB_RERANK_INSTRUCTION, KB_RERANK_MAX_DOCS,
KB_RERANK_TIMEOUT, RERANK_ADAPTER_LISTEN, RERANK_ADAPTER_PORT.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from volcengine.auth.SignerV4 import SignerV4
from volcengine.base.Request import Request
from volcengine.Credentials import Credentials

HOST = os.environ.get("KB_RERANK_HOST", "api-knowledgebase.mlp.cn-beijing.volces.com")
PATH = os.environ.get("KB_RERANK_PATH", "/api/knowledge/service/rerank")
SERVICE = os.environ.get("KB_RERANK_SERVICE", "air")
REGION = os.environ.get("KB_RERANK_REGION", "cn-beijing")
MODEL = os.environ.get("KB_RERANK_MODEL", "doubao-seed-rerank")
INSTRUCTION = os.environ.get(
    "KB_RERANK_INSTRUCTION",
    "Whether the Document answers the Query or matches the content retrieval intent",
)
MAX_DOCS = int(os.environ.get("KB_RERANK_MAX_DOCS", "200"))
TIMEOUT = float(os.environ.get("KB_RERANK_TIMEOUT", "30"))
LISTEN = os.environ.get("RERANK_ADAPTER_LISTEN", "0.0.0.0")
PORT = int(os.environ.get("RERANK_ADAPTER_PORT", "8808"))
CREDS_FILE = os.environ.get("KB_RERANK_CREDS", "/etc/openviking/rerank-kb.json")


def log(message: str) -> None:
    print(message, flush=True)


def load_credentials() -> tuple[str, str]:
    try:
        with open(CREDS_FILE, encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError as exc:
        log(f"fatal: cannot read credentials file {CREDS_FILE}: {exc}")
        sys.exit(1)
    except json.JSONDecodeError as exc:
        log(f"fatal: credentials file {CREDS_FILE} is not valid JSON: {exc}")
        sys.exit(1)
    ak = (data.get("ak") or "").strip()
    sk = (data.get("sk") or "").strip()
    if not ak or not sk:
        log(f"fatal: credentials file {CREDS_FILE} must contain non-empty ak and sk")
        sys.exit(1)
    return ak, sk


AK, SK = load_credentials()


def knowledge_base_rerank(query: str, documents: list[str]) -> dict:
    """Call the Knowledge Base rerank API and normalise its response."""
    usable = [(index, text if text and text.strip() else " ") for index, text in enumerate(documents)]
    usable = usable[:MAX_DOCS]
    body = {
        "rerank_model": MODEL,
        "rerank_instruction": INSTRUCTION,
        "datas": [{"query": query, "content": text} for _, text in usable],
    }

    request = Request()
    request.set_shema("https")
    request.set_method("POST")
    request.set_host(HOST)
    request.set_path(PATH)
    request.set_headers({"Accept": "application/json", "Content-Type": "application/json", "Host": HOST})
    request.set_body(json.dumps(body))
    SignerV4.sign(request, Credentials(AK, SK, SERVICE, REGION))

    payload = request.body.encode("utf-8")
    http_request = urllib.request.Request(
        f"https://{HOST}{PATH}",
        data=payload,
        headers=request.headers,
        method="POST",
    )
    with urllib.request.urlopen(http_request, timeout=TIMEOUT) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _reply(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        if self.path.rstrip("/") in ("/healthz", "/health"):
            self._reply(200, {"status": "ok", "model": MODEL, "upstream": HOST})
        else:
            self._reply(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        if self.path.rstrip("/") not in ("/v1/reranks", "/v1/rerank", "/reranks"):
            self._reply(404, {"error": "not found"})
            return

        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            self._reply(400, {"error": f"invalid JSON body: {exc}"})
            return

        query = body.get("query")
        documents = body.get("documents")
        if not isinstance(query, str) or not isinstance(documents, list) or not documents:
            self._reply(400, {"error": "query (string) and documents (non-empty list) are required"})
            return
        documents = [doc if isinstance(doc, str) else str(doc) for doc in documents]

        started = time.monotonic()
        try:
            upstream = knowledge_base_rerank(query, documents)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            log(f"upstream error status={exc.code} documents={len(documents)} detail={detail}")
            self._reply(502, {"error": f"knowledge base rerank failed: HTTP {exc.code}", "detail": detail})
            return
        except Exception as exc:  # noqa: BLE001 - surface any failure as a rerank error
            log(f"upstream error documents={len(documents)} error={type(exc).__name__}: {exc}")
            self._reply(502, {"error": f"knowledge base rerank failed: {type(exc).__name__}"})
            return

        scores = (upstream.get("data") or {}).get("scores")
        if upstream.get("code") not in (0, None) or not isinstance(scores, list):
            log(f"upstream rejected documents={len(documents)} response={json.dumps(upstream)[:300]}")
            self._reply(502, {"error": "knowledge base rerank returned no scores", "detail": upstream})
            return

        results = [
            {"index": index, "relevance_score": float(score)}
            for index, score in enumerate(scores)
            if index < len(documents)
        ]
        tokens = (upstream.get("data") or {}).get("token_usage")
        log(
            f"rerank ok documents={len(documents)} scored={len(results)} "
            f"tokens={tokens} elapsed_ms={(time.monotonic() - started) * 1000:.0f}"
        )
        self._reply(
            200,
            {
                "id": upstream.get("request_id"),
                "model": MODEL,
                "results": results,
                "usage": {"total_tokens": tokens or 0},
            },
        )

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003 - silence default access log
        return


if __name__ == "__main__":
    log(f"rerank adapter listening on {LISTEN}:{PORT} -> https://{HOST}{PATH} (service={SERVICE}, model={MODEL})")
    ThreadingHTTPServer((LISTEN, PORT), Handler).serve_forever()

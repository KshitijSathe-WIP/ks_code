"""
load_to_cosmosdb.py
-------------------
Loads Informatica lineage vertices and edges from a JSON file into
Azure Cosmos DB for Apache Gremlin.

Requires NO external packages — uses only Python stdlib.
Implements a minimal WebSocket + Gremlin client using ssl/socket/struct.

Two modes:
  (default)        Connect via WebSocket to Cosmos DB Gremlin and upsert all data
  --gremlin-script  Write chunked .gremlin files for Azure Portal Data Explorer

Environment variables:
  COSMOS_ACCOUNT  — account name only, e.g. tdbankpoc
  COSMOS_KEY      — primary key from Azure portal (base64 string as shown in portal)
  COSMOS_DB       — database name
  COSMOS_GRAPH    — graph/collection name

Usage:
  # Generate .gremlin script files (no credentials needed):
  python load_to_cosmosdb.py --lineage "Output Files/sample_lineage.json" --gremlin-script

  # Load directly via WebSocket (set env vars first):
  python load_to_cosmosdb.py --lineage "Output Files/sample_lineage.json"

  # Dry-run (print sample queries without connecting):
  python load_to_cosmosdb.py --lineage "Output Files/sample_lineage.json" --dry-run
"""

import argparse
import base64
import json
import os
import socket
import ssl
import struct
import sys
import time
import uuid
from pathlib import Path

# ---------------------------------------------------------------------------
# Gremlin query builders
# ---------------------------------------------------------------------------

def _esc(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("'", "\\'")


def vertex_upsert_query(v: dict, for_union: bool = False) -> str:
    """Return a Gremlin traversal for upserting a vertex.
    for_union=True: returns a __.V(...) step for embedding inside union().
    for_union=False: returns a full g.V(...) standalone statement.
    """
    vid = _esc(v["id"])
    label = _esc(v.get("label", "field"))
    props = "".join(
        f".property('{k}', '{_esc(v.get(k, ''))}')"
        for k in ("db_schema", "table_name", "field_name", "layer", "data_type", "precision")
    )
    prefix = "__" if for_union else "g"
    return (
        f"{prefix}.V('{vid}').fold().coalesce("
        f"__.unfold(),"
        f"__.addV('{label}').property('id','{vid}')){props}"
    )


def edge_upsert_query(e: dict, for_union: bool = False) -> str:
    """Return a Gremlin traversal for upserting an edge.
    for_union=True: returns a __.V(...) step for embedding inside union().
    """
    eid = _esc(e["id"])
    label = _esc(e.get("label", "transforms_to"))
    src = _esc(e["from_vertex"])
    tgt = _esc(e["to_vertex"])
    props = "".join(
        f".property('{k}', '{_esc(e.get(k, ''))}')"
        for k in ("mapping_name", "folder_name", "transformation_name", "transformation_type", "expression")
    )
    prefix = "__" if for_union else "g"
    return (
        f"{prefix}.V('{src}').as('s').V('{tgt}').as('t')"
        f".coalesce("
        f"__.inE('{label}').where(__.outV().hasId('{src}')).has('id','{eid}'),"
        f"__.addE('{label}').from('s').property('id','{eid}'){props})"
    )


# ---------------------------------------------------------------------------
# Minimal WebSocket + Gremlin client (stdlib only)
# ---------------------------------------------------------------------------

class GremlinWSClient:
    """
    Minimal Gremlin client over WebSocket/SSL using only Python stdlib.
    Implements RFC 6455 WebSocket framing + Gremlin GraphSON v2 protocol.
    Compatible with Azure Cosmos DB for Apache Gremlin.
    """

    MIME_TYPE = "application/vnd.gremlin-v2.0+json"

    def __init__(self, host: str, port: int, username: str, password: str,
                 timeout: int = 30, connect_host: str | None = None, verify_ssl: bool = True):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.timeout = timeout
        self.connect_host = connect_host or host
        self.verify_ssl = verify_ssl
        self._sock: ssl.SSLSocket | None = None

    # ---- connection ----

    def connect(self):
        ctx = ssl.create_default_context()
        if not self.verify_ssl:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        raw = socket.create_connection((self.connect_host, self.port), timeout=self.timeout)
        self._sock = ctx.wrap_socket(raw, server_hostname=self.host)
        self._ws_handshake()
        self._send_mime_type()
        self._authenticate()

    def _ws_handshake(self):
        nonce = base64.b64encode(os.urandom(16)).decode()
        handshake = (
            f"GET / HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {nonce}\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"Sec-WebSocket-Protocol: {self.MIME_TYPE}\r\n"
            f"\r\n"
        )
        self._sock.sendall(handshake.encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            resp += self._sock.recv(4096)
        if b"101" not in resp:
            raise RuntimeError(f"WebSocket handshake failed:\n{resp.decode(errors='replace')[:500]}")

    def _send_mime_type(self):
        """Send MIME type prefix as first binary frame (Gremlin Server protocol)."""
        mime = self.MIME_TYPE.encode()
        payload = bytes([len(mime)]) + mime
        self._send_frame(payload, opcode=0x2)  # binary frame

    def _authenticate(self):
        """Send SASL PLAIN authentication message."""
        sasl_bytes = ("\0" + self.username + "\0" + self.password).encode("utf-8")
        sasl_b64 = base64.b64encode(sasl_bytes).decode("utf-8")
        msg = {
            "requestId": str(uuid.uuid4()),
            "op": "authentication",
            "processor": "",
            "args": {"sasl": sasl_b64},
        }
        self._send_text(json.dumps(msg))
        resp = self._recv_message()
        code = resp.get("status", {}).get("code", 0)
        if code not in (200, 204):
            raise RuntimeError(f"Gremlin authentication failed (status {code}): {resp}")

    # ---- query execution ----

    def execute(self, query: str) -> dict:
        msg = {
            "requestId": str(uuid.uuid4()),
            "op": "eval",
            "processor": "",
            "args": {
                "gremlin": query,
                "bindings": {},
                "language": "gremlin-groovy",
                "aliases": {},
            },
        }
        self._send_text(json.dumps(msg))
        # Accumulate partial results until status code != 206
        result_data = []
        while True:
            resp = self._recv_message()
            code = resp.get("status", {}).get("code", 500)
            result_data.extend(resp.get("result", {}).get("data", []) or [])
            if code == 206:   # partial content — more frames coming
                continue
            if code not in (200, 204):
                msg_txt = resp.get("status", {}).get("message", "")
                raise RuntimeError(f"Gremlin error {code}: {msg_txt}")
            return {"status": code, "data": result_data}

    def close(self):
        if self._sock:
            try:
                # Send WebSocket close frame
                self._send_frame(struct.pack(">H", 1000), opcode=0x8)
            except Exception:
                pass
            self._sock.close()
            self._sock = None

    # ---- WebSocket framing ----

    def _send_frame(self, payload: bytes, opcode: int = 0x1):
        """Send a WebSocket frame (client-to-server, always masked)."""
        frame = bytearray()
        frame.append(0x80 | opcode)   # FIN bit + opcode
        length = len(payload)
        mask_key = os.urandom(4)
        if length < 126:
            frame.append(0x80 | length)
        elif length < 65536:
            frame.append(0x80 | 126)
            frame.extend(struct.pack(">H", length))
        else:
            frame.append(0x80 | 127)
            frame.extend(struct.pack(">Q", length))
        frame.extend(mask_key)
        frame.extend(bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload)))
        self._sock.sendall(bytes(frame))

    def _send_text(self, text: str):
        self._send_frame(text.encode("utf-8"), opcode=0x1)

    def _recv_exactly(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self._sock.recv(n - len(buf))
            if not chunk:
                raise RuntimeError("Connection closed by server")
            buf += chunk
        return buf

    def _recv_frame(self) -> tuple[int, bytes, bool]:
        header = self._recv_exactly(2)
        fin = bool(header[0] & 0x80)
        opcode = header[0] & 0x0F
        masked = bool(header[1] & 0x80)
        length = header[1] & 0x7F
        if length == 126:
            length = struct.unpack(">H", self._recv_exactly(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self._recv_exactly(8))[0]
        mask = self._recv_exactly(4) if masked else b""
        data = self._recv_exactly(length)
        if masked:
            data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        return opcode, data, fin

    def _recv_message(self) -> dict:
        """Receive a complete (possibly fragmented) WebSocket message and parse as JSON."""
        payload = b""
        while True:
            opcode, data, fin = self._recv_frame()
            if opcode == 0x8:   # close
                raise RuntimeError("Server closed WebSocket connection")
            if opcode == 0x9:   # ping
                self._send_frame(data, opcode=0xA)  # pong
                continue
            payload += data
            if fin:
                break
        return json.loads(payload.decode("utf-8"))


# ---------------------------------------------------------------------------
# Script file generator
# ---------------------------------------------------------------------------

CHUNK_SIZE = 100   # steps per union() — keeps each query under ~32 KB Cosmos DB limit


def _wrap_union(steps: list[str]) -> str:
    """Wrap a list of __.V()/__.addE() traversal steps into one g.inject(0).union(...) statement."""
    inner = ",\n  ".join(steps)
    return f"g.inject(0).union(\n  {inner}\n)"


def write_gremlin_scripts(lineage_path: Path, vertices: list, edges: list) -> list:
    """Write chunked .gremlin files where EACH FILE IS ONE QUERY using union().
    This means each file = one paste + one Execute in the Azure Portal.
    """
    out_dir = lineage_path.parent
    stem = lineage_path.stem

    # Delete any previously generated files for this stem
    for old in out_dir.glob(f"{stem}_part*.gremlin"):
        old.unlink()

    vertex_steps = [vertex_upsert_query(v, for_union=True) for v in vertices]
    edge_steps   = [edge_upsert_query(e,   for_union=True) for e in edges]
    all_steps = vertex_steps + edge_steps   # vertices must come before edges

    chunks = [all_steps[i:i + CHUNK_SIZE] for i in range(0, len(all_steps), CHUNK_SIZE)]
    files_written = []
    for idx, chunk in enumerate(chunks, 1):
        path = out_dir / f"{stem}_part{idx:04d}.gremlin"
        with open(path, "w", encoding="utf-8") as f:
            f.write(_wrap_union(chunk) + "\n")
        files_written.append(path)
    return files_written


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Load Informatica lineage into Azure Cosmos DB for Apache Gremlin")
    parser.add_argument("--lineage", required=True, help="Path to lineage JSON from extract_lineage.py")
    parser.add_argument("--gremlin-script", action="store_true",
                        help="Write chunked .gremlin script files (no credentials needed)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print sample queries without connecting")
    parser.add_argument("--ip", default=None,
                        help="Connect to this IP directly (bypass DNS), e.g. --ip 40.79.153.12")    parser.add_argument("--no-verify-ssl", action="store_true",
                        help="Disable TLS certificate verification (POC only — not for production)")    args = parser.parse_args()

    lineage_path = Path(args.lineage)
    if not lineage_path.exists():
        print(f"ERROR: Lineage file not found: {lineage_path}", file=sys.stderr)
        sys.exit(1)

    with open(lineage_path, encoding="utf-8") as f:
        data = json.load(f)

    vertices = data.get("vertices", [])
    edges = data.get("edges", [])

    print(f"Lineage file  : {lineage_path}")
    print(f"Field vertices: {len(vertices)}")
    print(f"Lineage edges : {len(edges)}")

    # --- Mode: generate .gremlin script files ---
    if args.gremlin_script:
        files = write_gremlin_scripts(lineage_path, vertices, edges)
        print(f"\nWritten {len(files)} .gremlin file(s) to: {lineage_path.parent}")
        print("To load: Azure Portal → Cosmos DB → Data Explorer → open Gremlin console")
        print("         paste each file's contents and execute in order.")
        return

    # --- Mode: dry run ---
    if args.dry_run:
        print("\n[DRY RUN] Sample vertex upsert (standalone):")
        print(" ", vertex_upsert_query(vertices[0]) if vertices else "(none)")
        print("\n[DRY RUN] Sample edge upsert (standalone):")
        print(" ", edge_upsert_query(edges[0]) if edges else "(none)")
        print("\n[DRY RUN] Sample union query (first 3 vertices):")
        print(" ", _wrap_union([vertex_upsert_query(v, for_union=True) for v in vertices[:3]]))
        print("\nDry run complete.")
        return

    # --- Mode: WebSocket load ---
    account = os.environ.get("COSMOS_ACCOUNT")
    key = os.environ.get("COSMOS_KEY")
    db = os.environ.get("COSMOS_DB")
    graph = os.environ.get("COSMOS_GRAPH")

    if not all([account, key, db, graph]):
        print(
            "ERROR: Set COSMOS_ACCOUNT, COSMOS_KEY, COSMOS_DB, COSMOS_GRAPH environment variables,\n"
            "       or use --gremlin-script to generate script files without connecting.",
            file=sys.stderr,
        )
        sys.exit(1)

    host = f"{account}.gremlin.cosmos.azure.com"
    connect_host = args.ip if args.ip else host
    username = f"/dbs/{db}/colls/{graph}"
    verify_ssl = not args.no_verify_ssl

    print(f"\nConnecting to: wss://{host}:443/")
    if args.ip:
        print(f"  (DNS bypassed — connecting to IP {args.ip}, SNI={host})")
    if not verify_ssl:
        print("  WARNING: TLS certificate verification disabled (POC mode)")
    client = GremlinWSClient(host=host, port=443, username=username, password=key,
                             connect_host=connect_host, verify_ssl=verify_ssl)
    try:
        client.connect()
        print("Connected and authenticated.")
    except Exception as ex:
        print(f"ERROR: Could not connect: {ex}", file=sys.stderr)
        sys.exit(1)

    errors = 0

    def run(query: str, label: str):
        nonlocal errors
        try:
            client.execute(query)
        except Exception as ex:
            print(f"  [WARN] {label}: {ex}")
            errors += 1

    print(f"\nUpserting {len(vertices)} vertices...")
    for i, v in enumerate(vertices):
        run(vertex_upsert_query(v, for_union=False), v["id"])
        if (i + 1) % 200 == 0:
            print(f"  {i + 1}/{len(vertices)} vertices done")
            time.sleep(0.1)

    print(f"\nUpserting {len(edges)} edges...")
    for i, e in enumerate(edges):
        run(edge_upsert_query(e, for_union=False), e["id"])
        if (i + 1) % 200 == 0:
            print(f"  {i + 1}/{len(edges)} edges done")
            time.sleep(0.1)

    client.close()
    print(f"\nLoad complete. Total: {len(vertices) + len(edges)}, Errors: {errors}")
    if errors:
        print("Re-run to retry failed items (upserts are idempotent).")


if __name__ == "__main__":
    main()

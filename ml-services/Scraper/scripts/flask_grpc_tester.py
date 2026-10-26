"""A small Flask app that calls the gRPC server the way your backend will.

Unlike grpc_server/rest_api.py (which calls the services directly and skips
gRPC), this goes through a real gRPC connection, so it tests the contract,
the port and the handlers.

1. Start the gRPC server:   uv run python -m grpc_server
2. Start this app:          uv run python scripts/flask_grpc_tester.py
3. Open:                    http://127.0.0.1:5050/

Needs Flask:  uv add --dev flask
"""
import os

import grpc
from flask import Flask, jsonify, request
from google.protobuf.json_format import MessageToDict

from grpc_server.proto import jobs_pb2 as pb
from grpc_server.proto import jobs_pb2_grpc as pb_grpc

GRPC_SERVER = os.getenv("GRPC_SERVER", "localhost:50051")
SCRAPE_TIMEOUT_SECONDS = 900   # a real scrape can take several minutes

app = Flask(__name__)
channel = grpc.insecure_channel(GRPC_SERVER)
scraper = pb_grpc.ScraperServiceStub(channel)
classifier = pb_grpc.ClassifierServiceStub(channel)


def csv_arg(name):
    """?sources=itpro,linkedin  ->  ["itpro", "linkedin"]"""
    return [v for v in request.args.get(name, "").split(",") if v]


@app.errorhandler(grpc.RpcError)
def grpc_error(err):
    code = err.code()
    status = {
        grpc.StatusCode.INVALID_ARGUMENT: 400,
        grpc.StatusCode.UNAVAILABLE: 503,
        grpc.StatusCode.DEADLINE_EXCEEDED: 504,
    }.get(code, 502)
    hint = "Is the gRPC server running (uv run python -m grpc_server)?" if status == 503 else None
    return jsonify(grpc_code=code.name, details=err.details(), hint=hint), status


@app.get("/")
def index():
    return jsonify(
        server=GRPC_SERVER,
        try_these=[
            "/ping",
            "/classify?title=SOC Analyst&description=SIEM, incident response",
            "/scrape-latest?sources=itpro&show=5",
            "/scrape-latest?sources=itpro&known_keys=itpro:15030&show=5",
            "/scrape-latest?sources=nope   (expect a 400 error)",
            "POST /classify with {'items': [{'key': 'k', 'title': '...', 'description': '...'}]}",
        ],
    )


@app.get("/ping")
def ping():
    """Only checks that a connection to the gRPC server can be opened."""
    try:
        grpc.channel_ready_future(channel).result(timeout=3)
        return jsonify(connected=True, server=GRPC_SERVER)
    except grpc.FutureTimeoutError:
        return jsonify(connected=False, server=GRPC_SERVER,
                       hint="Is the gRPC server running (uv run python -m grpc_server)?"), 503


@app.route("/classify", methods=["GET", "POST"])
def classify():
    if request.method == "POST":
        items = request.get_json(force=True)["items"]
    else:
        items = [{"key": "test", "title": request.args.get("title", ""),
                  "description": request.args.get("description", "")}]
    message = pb.ClassifyRequest(items=[pb.ClassifyItem(**i) for i in items])
    response = classifier.Classify(message, timeout=10)
    return jsonify([MessageToDict(r, preserving_proto_field_name=True) for r in response.results])


@app.get("/scrape-latest")
def scrape_latest():
    """Calls ScrapeLatest and collects the whole stream into one JSON answer.
    ?sources=itpro,linkedin  ?known_keys=itpro:1,itpro:2  ?limit=50  ?max_age_days=3
    ?show=20 limits how many jobs are printed (0 = all); the summary always has the totals."""
    message = pb.ScrapeLatestRequest(
        sources=csv_arg("sources"),
        known_keys=csv_arg("known_keys"),
        limit=request.args.get("limit", 0, type=int),
        max_age_days=request.args.get("max_age_days", 0, type=int),
    )
    show = request.args.get("show", 20, type=int)

    jobs, summary = [], None
    for event in scraper.ScrapeLatest(message, timeout=SCRAPE_TIMEOUT_SECONDS):
        if event.WhichOneof("event") == "job":
            jobs.append(MessageToDict(event.job, preserving_proto_field_name=True))
        else:
            summary = MessageToDict(event.summary, preserving_proto_field_name=True)

    return jsonify(summary=summary, jobs_received=len(jobs), jobs=jobs[:show] if show else jobs)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5050, debug=False)

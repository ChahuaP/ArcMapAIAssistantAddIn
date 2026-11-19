"""Process desktop GIS requests in one native runtime for the desktop session."""
import json
from pathlib import Path
from queue import SimpleQueue
from threading import Event, Thread

from guizhi.application.tasks import resident_gis, resident_progress
from guizhi.infrastructure.map_workspace import request
from guizhi.infrastructure.workspace import Workspace


def serve(input_stream, output):
    pending = SimpleQueue()
    stopped = Event()

    def receive():
        try:
            for line in input_stream:
                pending.put(line)
        finally:
            stopped.set()
            pending.put(None)

    Thread(target=receive, daemon=True).start()
    while (line := pending.get()) is not None and not stopped.is_set():
        message = json.loads(line)
        token = resident_gis.set(stopped.is_set)
        def notify(progress):
            print(json.dumps({"id": message["id"], "progress": progress}), file=output, flush=True)
        progress_token = resident_progress.set(notify)
        try:
            workspace = Workspace(Path(message["workspace"]), create=True)
            reply = {"result": request(workspace, message["map"])}
        except Exception as error:
            reply = {"error": str(error) or type(error).__name__, "errorCode": type(error).__name__}
        finally:
            resident_progress.reset(progress_token)
            resident_gis.reset(token)
        if not stopped.is_set():
            print(json.dumps({"id": message["id"], **reply}), file=output, flush=True)

from __future__ import annotations


def empty_workspace(ws, params):
    return ws.project()


class EmptyStrategy:
    strategy_id = "empty-strategy"

    def on_start(self, context):
        return ()

    def on_market(self, context):
        return ()

    def on_fill(self, fill, context):
        return ()

    def on_end(self, context):
        return ()

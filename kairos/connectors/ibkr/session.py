from __future__ import annotations


class IbkrSession:
    def __init__(self, host="127.0.0.1", port=4001, client_id=51, readonly=True) -> None:
        from ib_async import IB
        self.ib = IB()
        self.host, self.port, self.client_id, self.readonly = host, port, client_id, readonly
        self.contracts = {}

    def connect(self):
        if not self.ib.isConnected():
            self.ib.connect(self.host, self.port, clientId=self.client_id, readonly=self.readonly)

    def disconnect(self):
        if self.ib.isConnected():
            self.ib.disconnect()

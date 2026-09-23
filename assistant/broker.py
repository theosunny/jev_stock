"""A deliberately closed Guojin boundary. No credentials or order transport."""
from typing import Protocol


class BrokerAdapter(Protocol):
    def status(self) -> dict: ...
    def submit_order(self, order: dict) -> None: ...
    def cancel_order(self, order_id: str) -> None: ...


class GuojinDisabled:
    def status(self):
        return {'name': '国金证券', 'connected': False, 'live_enabled': False}

    def submit_order(self, order):
        raise PermissionError('国金证券尚未接入，实盘交易关闭')

    def cancel_order(self, order_id):
        raise PermissionError('国金证券尚未接入，实盘交易关闭')

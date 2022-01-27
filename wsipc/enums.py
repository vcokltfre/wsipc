from enum import IntEnum


class PayloadType(IntEnum):
    HEARTBEAT = 1
    HEARTBEAT_ACK = 2
    DATA = 3

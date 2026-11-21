"""Put audio on bus lanes from a test, the way a module's network source does: through the engine's `push`.

    bus = lane_bus(1, 2)          # a bus on the engine's fake backend with lanes 1 and 2 patched to a feed
    feed(bus, 1, tone)            # mono float32 at 48 kHz onto lane 1
"""
import numpy as np

from app import audiobus

SOURCE = "testfeed"          # not a registered source, so the bus opens nothing for it; the test is the source


def lane_bus(*lanes: int) -> "audiobus.Bus":
    bus = audiobus.Bus("fake")
    bus.apply({"enabled": True, "lanes": [{"lane": n, "source": SOURCE, "device": str(n), "channel": 0} for n in lanes]})
    return bus


def feed(bus: "audiobus.Bus", lane: int, block: np.ndarray) -> None:
    bus.engine.push(audiobus.input_id(SOURCE, str(lane)), np.asarray(block, dtype=np.float32).reshape(-1, 1), audiobus.RATE)

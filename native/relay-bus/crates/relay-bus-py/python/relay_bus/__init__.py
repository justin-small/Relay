"""Relay audio bus core, written in Rust (spike, issue 599). See native/relay-bus/README.md."""

from ._native import Bus, list_devices

__all__ = ["Bus", "list_devices"]

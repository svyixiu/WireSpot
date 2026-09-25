"""Persistent WFP rule that prevents hotspot traffic leaving outside NordVPN.

The rule is installed *before* tethering starts. It blocks forwarded IPv4
packets whose outgoing interface is not the validated VPN LUID and all
forwarded IPv6 packets. Rules persist if the CLI exits or crashes; WireSpot
removes only its own two filter keys after the hotspot has stopped.
"""
from __future__ import annotations

import ctypes as C
import sys
import uuid

from . import log


class ForwardGuardError(RuntimeError):
    pass


class GUID(C.Structure):
    _fields_ = [("data1", C.c_uint32), ("data2", C.c_uint16),
                ("data3", C.c_uint16), ("data4", C.c_ubyte * 8)]


def guid(value: str) -> GUID:
    return GUID.from_buffer_copy(uuid.UUID(value).bytes_le)


class Display(C.Structure):
    _fields_ = [("name", C.c_wchar_p), ("description", C.c_wchar_p)]


class Blob(C.Structure):
    _fields_ = [("size", C.c_uint32), ("data", C.c_void_p)]


class ValueUnion(C.Union):
    _fields_ = [("uint32", C.c_uint32), ("uint64", C.POINTER(C.c_uint64)),
                ("ptr", C.c_void_p)]


class Value(C.Structure):
    _fields_ = [("type", C.c_uint32), ("value", ValueUnion)]


class Condition(C.Structure):
    _fields_ = [("field", GUID), ("match", C.c_uint32), ("value", Value)]


class Action(C.Structure):
    _fields_ = [("type", C.c_uint32), ("filter_type", GUID)]


class ContextUnion(C.Union):
    _fields_ = [("raw", C.c_uint64), ("provider_context_key", GUID)]


class SubLayer(C.Structure):
    _fields_ = [("key", GUID), ("display", Display), ("flags", C.c_uint32),
                ("provider", C.c_void_p), ("provider_data", Blob), ("weight", C.c_uint16)]


class Filter(C.Structure):
    _fields_ = [("key", GUID), ("display", Display), ("flags", C.c_uint32),
                ("provider", C.c_void_p), ("provider_data", Blob), ("layer", GUID),
                ("sublayer", GUID), ("weight", Value), ("condition_count", C.c_uint32),
                ("conditions", C.POINTER(Condition)), ("action", Action),
                ("context", ContextUnion), ("reserved", C.c_void_p),
                ("filter_id", C.c_uint64), ("effective_weight", Value)]


V4_KEY = "9242ad73-cd78-43c7-9591-a9c094753340"
V6_KEY = "ab1e7cb1-521d-4460-86f9-22f73e4c0f39"
LAYER_V4 = "a82acc24-4ee1-4ee1-b465-fd1d25cb10a4"
LAYER_V6 = "7b964818-19c7-493a-b71f-832c3684d28c"
SUBLAYER_KEY = "71160483-9d6d-48eb-ad64-0c326acb388d"
OUT_INTERFACE = "1076b8a5-6323-4c5e-9810-e8d3fc9e6136"
FWP_UINT64 = 4
FWP_NOT_EQUAL = 10
FWP_ACTION_BLOCK = 0x1001
FWPM_FILTER_FLAG_PERSISTENT = 1
FWP_E_FILTER_NOT_FOUND = 0x80320003
FWP_E_SUBLAYER_NOT_FOUND = 0x80320007
FWP_E_ALREADY_EXISTS = 0x80320009


def _api():
    if sys.platform != "win32":
        raise ForwardGuardError("Windows Filtering Platform is available only on Windows")
    dll = C.WinDLL("fwpuclnt.dll")
    dll.FwpmEngineOpen0.argtypes = [C.c_wchar_p, C.c_uint32, C.c_void_p, C.c_void_p, C.POINTER(C.c_void_p)]
    dll.FwpmEngineOpen0.restype = C.c_uint32
    dll.FwpmEngineClose0.argtypes = [C.c_void_p]
    dll.FwpmEngineClose0.restype = C.c_uint32
    dll.FwpmFilterAdd0.argtypes = [C.c_void_p, C.POINTER(Filter), C.c_void_p, C.c_void_p]
    dll.FwpmFilterAdd0.restype = C.c_uint32
    dll.FwpmFilterDeleteByKey0.argtypes = [C.c_void_p, C.POINTER(GUID)]
    dll.FwpmFilterDeleteByKey0.restype = C.c_uint32
    dll.FwpmFilterGetByKey0.argtypes = [C.c_void_p, C.POINTER(GUID), C.POINTER(C.c_void_p)]
    dll.FwpmFilterGetByKey0.restype = C.c_uint32
    dll.FwpmSubLayerAdd0.argtypes = [C.c_void_p, C.POINTER(SubLayer), C.c_void_p]
    dll.FwpmSubLayerAdd0.restype = C.c_uint32
    dll.FwpmSubLayerDeleteByKey0.argtypes = [C.c_void_p, C.POINTER(GUID)]
    dll.FwpmSubLayerDeleteByKey0.restype = C.c_uint32
    dll.FwpmFreeMemory0.argtypes = [C.POINTER(C.c_void_p)]
    dll.FwpmFreeMemory0.restype = None
    return dll


class Engine:
    def __enter__(self):
        self.dll = _api()
        self.handle = C.c_void_p()
        code = self.dll.FwpmEngineOpen0(None, 10, None, None, C.byref(self.handle))
        if code:
            raise ForwardGuardError(f"Could not open Windows Filtering Platform (0x{code:08x})")
        return self

    def __exit__(self, *_):
        self.dll.FwpmEngineClose0(self.handle)

    def delete(self, key: str):
        k = guid(key)
        code = self.dll.FwpmFilterDeleteByKey0(self.handle, C.byref(k))
        if code not in (0, FWP_E_FILTER_NOT_FOUND):
            raise ForwardGuardError(f"Could not remove WireSpot forwarding rule (0x{code:08x})")

    def exists(self, key: str) -> bool:
        k, ptr = guid(key), C.c_void_p()
        code = self.dll.FwpmFilterGetByKey0(self.handle, C.byref(k), C.byref(ptr))
        if code == FWP_E_FILTER_NOT_FOUND:
            return False
        if code:
            raise ForwardGuardError(f"Could not inspect WireSpot forwarding rule (0x{code:08x})")
        self.dll.FwpmFreeMemory0(C.byref(ptr))
        return True

    def ensure_sublayer(self) -> None:
        layer = SubLayer()
        layer.key = guid(SUBLAYER_KEY)
        layer.display = Display("WireSpot NordVPN safety", "Forwarded traffic may use only the selected VPN tunnel")
        layer.flags = 1  # FWPM_SUBLAYER_FLAG_PERSISTENT
        layer.weight = 0xFFFF
        code = self.dll.FwpmSubLayerAdd0(self.handle, C.byref(layer), None)
        if code not in (0, FWP_E_ALREADY_EXISTS):
            raise ForwardGuardError(f"Could not install WireSpot filtering sublayer (0x{code:08x})")

    def delete_sublayer(self) -> None:
        key = guid(SUBLAYER_KEY)
        code = self.dll.FwpmSubLayerDeleteByKey0(self.handle, C.byref(key))
        if code not in (0, FWP_E_SUBLAYER_NOT_FOUND):
            raise ForwardGuardError(f"Could not remove WireSpot filtering sublayer (0x{code:08x})")

    def add(self, key: str, layer: str, outgoing_luid: int | None):
        f = Filter()
        f.key = guid(key)
        f.display = Display("WireSpot NordVPN forwarding guard",
                            "Blocks hotspot forwarding outside the selected NordVPN tunnel")
        f.flags = FWPM_FILTER_FLAG_PERSISTENT
        f.layer, f.sublayer = guid(layer), guid(SUBLAYER_KEY)
        f.action.type = FWP_ACTION_BLOCK
        # Keep the referenced condition data alive through the native call.
        if outgoing_luid is not None:
            luid = C.c_uint64(outgoing_luid)
            condition = Condition()
            condition.field = guid(OUT_INTERFACE)
            condition.match = FWP_NOT_EQUAL
            condition.value.type = FWP_UINT64
            condition.value.value.uint64 = C.pointer(luid)
            f.condition_count = 1
            f.conditions = C.pointer(condition)
        code = self.dll.FwpmFilterAdd0(self.handle, C.byref(f), None, None)
        if code:
            raise ForwardGuardError(f"Could not install forwarding rule (0x{code:08x})")


def interface_luid(interface_guid: str) -> int:
    if sys.platform != "win32":
        raise ForwardGuardError("Windows required")
    api = C.WinDLL("iphlpapi.dll")
    api.ConvertInterfaceGuidToLuid.argtypes = [C.POINTER(GUID), C.POINTER(C.c_uint64)]
    api.ConvertInterfaceGuidToLuid.restype = C.c_uint32
    key, luid = guid(interface_guid), C.c_uint64()
    code = api.ConvertInterfaceGuidToLuid(C.byref(key), C.byref(luid))
    if code or not luid.value:
        raise ForwardGuardError(f"Could not identify the NordVPN interface (Windows error {code})")
    return luid.value


def install(interface_guid: str) -> None:
    luid = interface_luid(interface_guid)
    with Engine() as engine:
        # A prior crash may have left these persistent, blocking rules behind.
        # Replace them only while the WireSpot operation lock is held.
        engine.delete(V4_KEY)
        engine.delete(V6_KEY)
        engine.ensure_sublayer()
        try:
            engine.add(V4_KEY, LAYER_V4, luid)
            engine.add(V6_KEY, LAYER_V6, None)
        except Exception:
            engine.delete(V4_KEY)
            engine.delete(V6_KEY)
            engine.delete_sublayer()
            raise
    log.event("info", "[ProfileLess] Downstream forwarding guard installed")


def remove() -> None:
    with Engine() as engine:
        engine.delete(V4_KEY)
        engine.delete(V6_KEY)
        engine.delete_sublayer()
    log.event("info", "[ProfileLess] Downstream forwarding guard removed")


def installed() -> bool:
    with Engine() as engine:
        return engine.exists(V4_KEY) and engine.exists(V6_KEY)

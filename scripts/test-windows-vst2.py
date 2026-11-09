#!/usr/bin/python3
"""Load every bridged VST2 plugin and exercise parameters and audio processing.

Usage: test-windows-vst2.py [DIRECTORY] (default ~/.vst/yabridge, searched recursively).
"""

import ctypes
import json
import math
from pathlib import Path

IP = ctypes.c_ssize_t
CALLBACK = ctypes.CFUNCTYPE(IP, ctypes.c_void_p, ctypes.c_int32, ctypes.c_int32, IP, ctypes.c_void_p, ctypes.c_float)


class Effect(ctypes.Structure):
    _fields_ = [
        ('magic', ctypes.c_int32),
        ('dispatcher', ctypes.c_void_p),
        ('process', ctypes.c_void_p),
        ('setParameter', ctypes.c_void_p),
        ('getParameter', ctypes.c_void_p),
        ('numPrograms', ctypes.c_int32),
        ('numParams', ctypes.c_int32),
        ('numInputs', ctypes.c_int32),
        ('numOutputs', ctypes.c_int32),
        ('flags', ctypes.c_int32),
        ('resvd1', IP),
        ('resvd2', IP),
        ('initialDelay', ctypes.c_int32),
        ('realQualities', ctypes.c_int32),
        ('offQualities', ctypes.c_int32),
        ('ioRatio', ctypes.c_float),
        ('object', ctypes.c_void_p),
        ('user', ctypes.c_void_p),
        ('uniqueID', ctypes.c_int32),
        ('version', ctypes.c_int32),
        ('processReplacing', ctypes.c_void_p),
        ('processDoubleReplacing', ctypes.c_void_p),
        ('future', ctypes.c_char * 56),
    ]


@CALLBACK
def host(effect, opcode, index, value, ptr, opt):
    if opcode == 1:
        return 2400
    if opcode == 15:
        return 48000
    if opcode == 16:
        return 256
    if opcode == 23:
        return 2
    if opcode in (32, 33) and ptr:
        ctypes.memmove(ptr, b'Shaffy plugin verification\0', 27)
        return 1
    if opcode == 34:
        return 1
    return 0


def verify(path):
    library = ctypes.CDLL(str(path))
    entry = library.VSTPluginMain
    entry.argtypes = [CALLBACK]
    entry.restype = ctypes.POINTER(Effect)
    pointer = entry(host)
    assert pointer and pointer.contents.magic == 0x56737450, 'Invalid VST2 instance'
    effect = pointer.contents
    dispatch = CALLBACK(effect.dispatcher)
    dispatch(pointer, 0, 0, 0, None, 0)
    name = ctypes.create_string_buffer(128)
    dispatch(pointer, 45, 0, 0, name, 0)
    dispatch(pointer, 10, 0, 0, None, 48000)
    dispatch(pointer, 11, 0, 256, None, 0)
    parameter_ok = None
    if effect.numParams:
        get = ctypes.CFUNCTYPE(ctypes.c_float, ctypes.c_void_p, ctypes.c_int32)(effect.getParameter)
        set_ = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_int32, ctypes.c_float)(effect.setParameter)
        before = get(pointer, 0)
        set_(pointer, 0, before)
        parameter_ok = abs(get(pointer, 0) - before) < 1e-5
        assert parameter_ok, 'Parameter round trip failed'
    frames = 256
    inputs = [
        (ctypes.c_float * frames)(*[0.05 * math.sin(i * 0.04) for i in range(frames)]) for _ in range(effect.numInputs)
    ]
    outputs = [(ctypes.c_float * frames)() for _ in range(effect.numOutputs)]
    input_ptrs = (ctypes.POINTER(ctypes.c_float) * len(inputs))(*inputs)
    output_ptrs = (ctypes.POINTER(ctypes.c_float) * len(outputs))(*outputs)
    dispatch(pointer, 12, 0, 1, None, 0)
    dispatch(pointer, 71, 0, 0, None, 0)
    assert effect.processReplacing, 'Missing processReplacing callback'
    process = ctypes.CFUNCTYPE(
        None,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.POINTER(ctypes.c_float)),
        ctypes.POINTER(ctypes.POINTER(ctypes.c_float)),
        ctypes.c_int32,
    )(effect.processReplacing)
    peak = 0.0
    # ReaFIR buffers a full FFT window before producing its first output block.
    for _ in range(max(64, effect.initialDelay // frames + 32)):
        process(pointer, input_ptrs, output_ptrs, frames)
        for channel in outputs:
            assert all(math.isfinite(v) for v in channel), 'Non-finite output'
            peak = max(peak, max(map(abs, channel), default=0))
    # The gate intentionally silences a below-threshold input in its default state.
    if path.name != 'reagate-standalone.so':
        assert peak > 0, 'No output after allowing for plugin latency'
    dispatch(pointer, 72, 0, 0, None, 0)
    dispatch(pointer, 12, 0, 0, None, 0)
    result = {
        'plugin': path.name,
        'name': name.value.decode(errors='replace'),
        'inputs': effect.numInputs,
        'outputs': effect.numOutputs,
        'parameter_roundtrip': parameter_ok,
        'output_peak': peak,
    }
    dispatch(pointer, 1, 0, 0, None, 0)
    return result


if __name__ == '__main__':
    import sys

    directory = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / '.vst/yabridge'
    plugins = sorted(directory.rglob('*.so'))
    assert plugins, f'No bridged plugins in {directory}; run yabridgectl sync first'
    for plugin in plugins:
        print(json.dumps(verify(plugin)), flush=True)

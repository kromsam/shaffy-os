#!/usr/bin/env python3
"""Load every bridged VST2 plugin and exercise parameters and audio processing.

Usage: test-windows-vst2.py [DIRECTORY] (default ~/.vst/yabridge, searched recursively).
"""
import ctypes as C
import json
import math
from pathlib import Path

IP = C.c_ssize_t
CALLBACK = C.CFUNCTYPE(IP, C.c_void_p, C.c_int32, C.c_int32, IP, C.c_void_p, C.c_float)


class Effect(C.Structure):
    _fields_ = [('magic', C.c_int32), ('dispatcher', C.c_void_p),
                ('process', C.c_void_p), ('setParameter', C.c_void_p),
                ('getParameter', C.c_void_p), ('numPrograms', C.c_int32),
                ('numParams', C.c_int32), ('numInputs', C.c_int32),
                ('numOutputs', C.c_int32), ('flags', C.c_int32),
                ('resvd1', IP), ('resvd2', IP), ('initialDelay', C.c_int32),
                ('realQualities', C.c_int32), ('offQualities', C.c_int32),
                ('ioRatio', C.c_float), ('object', C.c_void_p), ('user', C.c_void_p),
                ('uniqueID', C.c_int32), ('version', C.c_int32),
                ('processReplacing', C.c_void_p), ('processDoubleReplacing', C.c_void_p),
                ('future', C.c_char * 56)]


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
        C.memmove(ptr, b'Shaffy plugin verification\0', 27)
        return 1
    if opcode == 34:
        return 1
    return 0


def verify(path):
    library = C.CDLL(str(path))
    entry = library.VSTPluginMain
    entry.argtypes = [CALLBACK]
    entry.restype = C.POINTER(Effect)
    pointer = entry(host)
    assert pointer and pointer.contents.magic == 0x56737450, 'Invalid VST2 instance'
    effect = pointer.contents
    dispatch = CALLBACK(effect.dispatcher)
    dispatch(pointer, 0, 0, 0, None, 0)
    name = C.create_string_buffer(128)
    dispatch(pointer, 45, 0, 0, name, 0)
    dispatch(pointer, 10, 0, 0, None, 48000)
    dispatch(pointer, 11, 0, 256, None, 0)
    parameter_ok = None
    if effect.numParams:
        get = C.CFUNCTYPE(C.c_float, C.c_void_p, C.c_int32)(effect.getParameter)
        set_ = C.CFUNCTYPE(None, C.c_void_p, C.c_int32, C.c_float)(effect.setParameter)
        before = get(pointer, 0)
        set_(pointer, 0, before)
        parameter_ok = abs(get(pointer, 0) - before) < 1e-5
        assert parameter_ok, 'Parameter round trip failed'
    frames = 256
    inputs = [(C.c_float * frames)(*[0.05 * math.sin(i * 0.04) for i in range(frames)])
              for _ in range(effect.numInputs)]
    outputs = [(C.c_float * frames)() for _ in range(effect.numOutputs)]
    input_ptrs = (C.POINTER(C.c_float) * len(inputs))(*inputs)
    output_ptrs = (C.POINTER(C.c_float) * len(outputs))(*outputs)
    dispatch(pointer, 12, 0, 1, None, 0)
    dispatch(pointer, 71, 0, 0, None, 0)
    assert effect.processReplacing, 'Missing processReplacing callback'
    process = C.CFUNCTYPE(None, C.c_void_p, C.POINTER(C.POINTER(C.c_float)),
                         C.POINTER(C.POINTER(C.c_float)), C.c_int32)(effect.processReplacing)
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
    result = {'plugin': path.name, 'name': name.value.decode(errors='replace'),
              'inputs': effect.numInputs, 'outputs': effect.numOutputs,
              'parameter_roundtrip': parameter_ok, 'output_peak': peak}
    dispatch(pointer, 1, 0, 0, None, 0)
    return result


if __name__ == '__main__':
    import sys
    directory = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / '.vst/yabridge'
    plugins = sorted(directory.rglob('*.so'))
    assert plugins, f'No bridged plugins in {directory}; run yabridgectl sync first'
    for plugin in plugins:
        print(json.dumps(verify(plugin)), flush=True)

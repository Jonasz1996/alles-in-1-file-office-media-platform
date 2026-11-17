"""Een ONNX-model schrijven met alleen de standaardbibliotheek (fase 6, Keuze 73 en 83; uit de
spike, B2). Van X3a; de nepmodellen van X2a gebruiken hem ook.

ONNX is protobuf. Deze module schrijft de paar berichten die een model nodig heeft zelf als bytes
op de draad: ModelProto, GraphProto, NodeProto, AttributeProto, TensorProto, ValueInfoProto en
OperatorSetIdProto, met de veldnummers van onnx/onnx.proto. Geen `onnx`, geen `protobuf` en geen
numpy: de omzetter van SigLIP (app/analyse/worker/omzetten/) geeft zijn numpy-arrays als buffer.

    g = Graph("naam")
    x = g.input("x", FLOAT, [1, "n", 4])
    w = g.initializer("w", FLOAT, [4, 2], pack(FLOAT, [0.5] * 8))
    y = g.op("MatMul", [x, w], outputs="y")
    g.output("y", FLOAT, [1, "n", 2])
    model = Model(g)            # IR 8, opset 17 (en com.microsoft 1 als een node dat vraagt)
    model.write("m.onnx")       # Written(size, sha256)

- **Altijd dezelfde bytes.** Dezelfde aanroepen geven dezelfde sha256: de velden staan in de
  volgorde van hun nummer (zoals protobuf zelf schrijft), herhaalde getallen los (onnx.proto is
  proto2, niet packed), de attributen van een node op naam, de opsets op domein. Automatische
  namen tellen in de volgorde van de aanroepen. De sha256 van een omgezet model staat in
  models.lock: verandert deze module de bytes, dan verandert die sha256 ook (test_onnxw.py legt
  er één vast).
- **De gegevens van een tensor** zijn little-endian en aaneengesloten: bytes, bytearray,
  array.array, memoryview of alles met het bufferprotocol (een numpy-array of numpy.memmap).
  Heeft de buffer een eigen formaat (niet "B"), dan moet dat bij het type passen: een float64-array
  als FLOAT is een fout, net als een vorm die niet bij het aantal bytes past. Of een callable die
  die buffer pas bij het schrijven geeft: de grootte volgt al uit vorm en type, dus de omzetter
  hoeft niet alle gewichten tegelijk in het geheugen te houden.
- **Schrijven gaat in stukken.** Model.write schrijft de koppen en de buffers rechtstreeks naar
  het bestand en rekent onderweg de sha256; het hele model staat nooit als één bytes-object in het
  geheugen. Naar een pad schrijft hij eerst `<naam>.deel` (nooit over een bestaand bestand of een
  symlink heen), dan fsync en os.replace.
- **Fouten** zijn OnnxwError (een ValueError) met de naam van de tensor, het attribuut of de
  waarde, meteen bij de aanroep die niet klopt; voor een callable bij het schrijven.
"""

from __future__ import annotations

import hashlib
import math
import numbers
import operator
import os
import struct
import sys
from collections.abc import Buffer, Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

# TensorProto.DataType
FLOAT = 1
UINT8 = 2
INT8 = 3
INT32 = 6
INT64 = 7
FLOAT16 = 10

# type -> (naam, bytes per element, struct-teken, formaten van een buffer die erbij passen)
_TYPES: dict[int, tuple[str, int, str, frozenset[str]]] = {
    FLOAT: ("float32", 4, "f", frozenset("f")),
    UINT8: ("uint8", 1, "B", frozenset("B")),
    INT8: ("int8", 1, "b", frozenset("b")),
    INT32: ("int32", 4, "i", frozenset("il")),
    INT64: ("int64", 8, "q", frozenset("ql")),
    FLOAT16: ("float16", 2, "e", frozenset("e")),
}

IR_VERSION = 8
OPSET = 17
MS_DOMAIN = "com.microsoft"
# De versie per domein; "" (de standaard-ops) staat altijd in het model, een ander domein alleen
# als een node het gebruikt
OPSETS: dict[str, int] = {"": OPSET, MS_DOMAIN: 1}
PRODUCER = "platform-onnxw"
# Eén protobuf-bericht is hooguit 2 GiB; groter kan ONNX alleen met externe gegevens
MAX_BYTES = 2**31 - 1
_INT64 = (-(2**63), 2**63 - 1)

# AttributeProto.AttributeType
_A_FLOAT, _A_INT, _A_STRING, _A_TENSOR, _A_FLOATS, _A_INTS = 1, 2, 3, 4, 6, 7

# De gegevens van een tensor: een buffer, of een callable die er bij het schrijven een geeft
Data = Buffer | Callable[[], Buffer]


class OnnxwError(ValueError):
    """Een aanroep die geen geldig model geeft: een onbekend type, een vorm die niet bij de bytes
    past, een naam die er al is of niet bestaat, een attribuut dat niet kan."""


@dataclass(frozen=True)
class Written:
    """Wat Model.write schreef: het aantal bytes en de sha256 (64 kleine hex)."""

    size: int
    sha256: str


def _type(dtype: object) -> tuple[str, int, str, frozenset[str]]:
    if isinstance(dtype, bool) or not isinstance(dtype, int) or dtype not in _TYPES:
        known = ", ".join(f"{v[0]} ({k})" for k, v in _TYPES.items())
        raise OnnxwError(f"onbekend type {dtype!r}; dit kan: {known}")
    return _TYPES[dtype]


def _int(value: object, what: str, lo: int = _INT64[0], hi: int = _INT64[1]) -> int:
    if isinstance(value, bool) or not isinstance(value, numbers.Integral):
        raise OnnxwError(f"{what}: {value!r} is geen geheel getal")
    n = operator.index(value)
    if not lo <= n <= hi:
        raise OnnxwError(f"{what}: {n} ligt buiten {lo}..{hi}")
    return n


def _name(value: object, what: str) -> str:
    if not isinstance(value, str) or not value:
        raise OnnxwError(f"{what}: {value!r} is geen naam (een niet-lege str)")
    return value


def _dims(dims: object, what: str) -> tuple[int, ...]:
    if not isinstance(dims, (list, tuple)):
        raise OnnxwError(f"{what}: de vorm {dims!r} is geen lijst of tuple")
    return tuple(_int(d, f"{what}, as {i}", 0) for i, d in enumerate(dims))


def pack(dtype: int, values: Sequence[float]) -> bytes:
    """Getallen als little-endian bytes van dit type, voor wie zonder numpy bouwt."""
    name, _, code, _ = _type(dtype)
    try:
        vals = list(values)
        return struct.pack(f"<{len(vals)}{code}", *vals)
    except (struct.error, OverflowError, TypeError) as e:
        raise OnnxwError(f"pack als {name}: {e}") from None


def _view(data: object, dtype: int, nbytes: int, what: str) -> memoryview:
    """De gegevens als memoryview van bytes, zonder kopie; elke controle met een eigen fout."""
    name, size, _, formats = _TYPES[dtype]
    if isinstance(data, str):
        raise OnnxwError(f"{what}: de gegevens zijn tekst, geen bytes")
    try:
        view = memoryview(data)  # type: ignore[arg-type]
    except TypeError:
        raise OnnxwError(
            f"{what}: {type(data).__name__} is geen buffer "
            "(bytes, bytearray, array.array, memoryview of een numpy-array)"
        ) from None
    fmt = view.format
    if fmt != "B":
        order, code = (fmt[0], fmt[1:]) if fmt[0] in "@=<>!" else ("@", fmt)
        if view.itemsize > 1 and (order in ">!" or (order in "@=" and sys.byteorder != "little")):
            raise OnnxwError(f"{what}: de buffer is big-endian ({fmt!r}), ONNX is little-endian")
        if code not in formats or view.itemsize != size:
            raise OnnxwError(f"{what}: een buffer met formaat {fmt!r} past niet bij {name}")
    if not view.c_contiguous:
        raise OnnxwError(f"{what}: de buffer is niet aaneengesloten (C-volgorde)")
    if view.nbytes != nbytes:
        raise OnnxwError(f"{what}: {view.nbytes} bytes, maar de vorm als {name} vraagt {nbytes}")
    return view.cast("B") if nbytes else memoryview(b"")


class Tensor:
    """Een tensor: een initializer van de graaf, of de waarde van een attribuut (dan mag de naam
    leeg zijn). data is een buffer of een callable die er een geeft (zie de moduletekst)."""

    __slots__ = ("name", "dtype", "dims", "nbytes", "_data")

    def __init__(self, name: str, dtype: int, dims: Sequence[int], data: Data) -> None:
        if not isinstance(name, str):
            raise OnnxwError(f"tensor {name!r}: de naam is geen str")
        what = f"tensor {name!r}"
        self.name = name
        self.dtype = dtype
        _, size, _, _ = _type(dtype)
        self.dims = _dims(dims, what)
        self.nbytes = math.prod(self.dims) * size
        self._data = data if callable(data) else _view(data, dtype, self.nbytes, what)

    def buffer(self) -> memoryview:
        """De bytes; een callable wordt hier aangeroepen (bij elke keer schrijven één keer)."""
        if isinstance(self._data, memoryview):
            return self._data
        return _view(self._data(), self.dtype, self.nbytes, f"tensor {self.name!r} (callable)")


# --- protobuf op de draad -------------------------------------------------------------------


def _varint(n: int) -> bytes:
    if n < 0:
        n += 1 << 64  # int64 als twee-complement: tien bytes, zoals protobuf
    out = bytearray()
    while n > 0x7F:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    out.append(n)
    return bytes(out)


def _key(field: int, wire: int) -> bytes:
    return _varint(field << 3 | wire)


class _Msg:
    """Een bericht in stukken: bytearrays met koppen en kleine velden, en de tensors waarvan de
    gegevens pas bij het schrijven gelezen worden. size telt alles."""

    __slots__ = ("parts", "size")

    def __init__(self) -> None:
        self.parts: list[bytearray | Tensor] = []
        self.size = 0

    def raw(self, b: bytes | bytearray) -> None:
        if self.parts and isinstance(self.parts[-1], bytearray):
            self.parts[-1] += b
        else:
            self.parts.append(bytearray(b))
        self.size += len(b)

    def varint(self, field: int, n: int) -> None:
        self.raw(_key(field, 0) + _varint(n))

    def fixed32(self, field: int, x: float) -> None:
        self.raw(_key(field, 5) + struct.pack("<f", x))

    def blob(self, field: int, b: bytes) -> None:
        self.raw(_key(field, 2) + _varint(len(b)) + b)

    def text(self, field: int, s: str) -> None:
        self.blob(field, s.encode("utf-8"))

    def msg(self, field: int, sub: _Msg) -> None:
        self.raw(_key(field, 2) + _varint(sub.size))
        for part in sub.parts:
            if isinstance(part, bytearray):
                self.raw(part)
            else:
                self.parts.append(part)
                self.size += part.nbytes

    def data(self, field: int, tensor: Tensor) -> None:
        self.raw(_key(field, 2) + _varint(tensor.nbytes))
        if tensor.nbytes:
            self.parts.append(tensor)
            self.size += tensor.nbytes


def _tensor(t: Tensor) -> _Msg:
    """TensorProto: dims 1, data_type 2, name 8, raw_data 9."""
    m = _Msg()
    for d in t.dims:
        m.varint(1, d)
    m.varint(2, t.dtype)
    if t.name:
        m.text(8, t.name)
    m.data(9, t)
    return m


def _value_info(name: str, dtype: int, shape: Sequence[int | str | None] | None) -> _Msg:
    """ValueInfoProto{name 1, type 2: TypeProto{tensor_type 1: {elem_type 1, shape 2:
    TensorShapeProto{dim 1: {dim_value 1 | dim_param 2}}}}}. Een as is een getal, een naam
    (symbolisch) of None (onbekend); shape None is een onbekende rang."""
    tensor_type = _Msg()
    tensor_type.varint(1, dtype)
    if shape is not None:
        dims = _Msg()
        for d in shape:
            dim = _Msg()
            if isinstance(d, str):
                dim.text(2, d)
            elif d is not None:
                dim.varint(1, d)
            dims.msg(1, dim)
        tensor_type.msg(2, dims)
    type_proto = _Msg()
    type_proto.msg(1, tensor_type)
    m = _Msg()
    m.text(1, name)
    m.msg(2, type_proto)
    return m


def _shape(shape: object, what: str) -> tuple[int | str | None, ...] | None:
    if shape is None:
        return None
    if not isinstance(shape, (list, tuple)):
        raise OnnxwError(f"{what}: de vorm {shape!r} is geen lijst, tuple of None")
    out: list[int | str | None] = []
    for i, d in enumerate(shape):
        if d is None or (isinstance(d, str) and d):
            out.append(d)
        elif isinstance(d, str):
            raise OnnxwError(f"{what}, as {i}: een symbolische as heeft een naam")
        else:
            out.append(_int(d, f"{what}, as {i}", 0))
    return tuple(out)


def _attribute(name: str, value: object, what: str) -> _Msg:
    """AttributeProto: name 1, f 2, i 3, s 4, t 5, floats 7, ints 8, type 20."""
    m = _Msg()
    m.text(1, name)
    what = f"{what}, attribuut {name!r}"

    def f32(x: object) -> float:
        try:
            struct.pack("<f", x)  # type: ignore[arg-type]
        except OverflowError:
            raise OnnxwError(f"{what}: {x!r} past niet in float32") from None
        return float(x)  # type: ignore[arg-type]

    if isinstance(value, numbers.Integral):
        m.varint(3, _int(value, what))
        m.varint(20, _A_INT)
    elif isinstance(value, numbers.Real):
        m.fixed32(2, f32(value))
        m.varint(20, _A_FLOAT)
    elif isinstance(value, (str, bytes)):
        m.blob(4, value.encode("utf-8") if isinstance(value, str) else value)
        m.varint(20, _A_STRING)
    elif isinstance(value, Tensor):
        m.msg(5, _tensor(value))
        m.varint(20, _A_TENSOR)
    elif isinstance(value, (list, tuple)) and value:
        if all(isinstance(v, numbers.Integral) for v in value):
            for v in value:
                m.varint(8, _int(v, what))
            m.varint(20, _A_INTS)
        elif all(isinstance(v, numbers.Real) for v in value):
            for v in value:
                m.fixed32(7, f32(v))
            m.varint(20, _A_FLOATS)
        else:
            raise OnnxwError(f"{what}: een lijst van alleen gehele getallen of alleen getallen")
    elif isinstance(value, (list, tuple)):
        raise OnnxwError(f"{what}: een lege lijst heeft geen type (ints of floats)")
    else:
        raise OnnxwError(f"{what}: {type(value).__name__} kan niet (int, float, str, Tensor)")
    return m


@dataclass(frozen=True)
class _Node:
    op_type: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    name: str
    domain: str
    attrs: tuple[_Msg, ...]


class Graph:
    """Een graaf, op volgorde: elke node gebruikt alleen namen die er al zijn (invoer,
    initializers en de uitvoer van eerdere nodes), en elke naam bestaat één keer."""

    def __init__(self, name: str) -> None:
        self.name = _name(name, "de graaf")
        self._inputs: list[_Msg] = []
        self._outputs: list[_Msg] = []
        self._out_names: set[str] = set()
        self._inits: list[Tensor] = []
        self._nodes: list[_Node] = []
        self._values: set[str] = set()
        self._node_names: set[str] = set()
        self._count = 0

    def _new(self, prefix: str) -> str:
        self._count += 1
        return f"{prefix}{self._count}"

    def _add(self, name: str, what: str) -> str:
        if name in self._values:
            raise OnnxwError(f"{what}: de naam {name!r} bestaat al in de graaf")
        self._values.add(name)
        return name

    def input(self, name: str, dtype: int, shape: Sequence[int | str | None] | None) -> str:
        what = f"invoer {name!r}"
        _type(dtype)
        info = _value_info(_name(name, "invoer"), dtype, _shape(shape, what))
        self._add(name, what)
        self._inputs.append(info)
        return name

    def initializer(self, name: str | None, dtype: int, dims: Sequence[int], data: Data) -> str:
        """Een constante tensor; name None geeft een eigen naam (c1, c2, …)."""
        name = self._new("c") if name is None else _name(name, "initializer")
        tensor = Tensor(name, dtype, dims, data)
        self._add(name, f"initializer {name!r}")
        self._inits.append(tensor)
        return name

    def op(
        self,
        op_type: str,
        inputs: Sequence[str],
        outputs: int | str | Sequence[str] = 1,
        *,
        domain: str = "",
        name: str | None = None,
        **attrs: object,
    ) -> str | list[str]:
        """Een node. outputs is een aantal (eigen namen t1, t2, …), een naam of een lijst namen;
        terug komt één naam, of een lijst als outputs een aantal > 1 of een lijst was. Een lege
        naam in inputs is een weggelaten optionele invoer. De attributen gaan op naam; een
        attribuut dat outputs, domain of name heet, kan hier niet (geen op van de omzetter of de
        nepmodellen heeft er een)."""
        op_type = _name(op_type, "op")
        what = f"node {op_type}"
        if not isinstance(domain, str):
            raise OnnxwError(f"{what}: het domein {domain!r} is geen str")
        if isinstance(inputs, str) or not isinstance(inputs, (list, tuple)):
            raise OnnxwError(f"{what}: inputs is geen lijst of tuple van namen")
        for i in inputs:
            if not isinstance(i, str):
                raise OnnxwError(f"{what}: invoer {i!r} is geen naam")
            if i and i not in self._values:
                raise OnnxwError(f"{what}: invoer {i!r} bestaat (nog) niet in de graaf")
        if isinstance(outputs, str):
            outs, single = [_name(outputs, f"{what}, uitvoer")], True
        elif isinstance(outputs, (list, tuple)):
            if not outputs:
                raise OnnxwError(f"{what}: geen uitvoer")
            outs, single = [_name(o, f"{what}, uitvoer") for o in outputs], False
        else:
            n = _int(outputs, f"{what}, aantal uitvoer", 1, 1000)
            outs, single = [self._new("t") for _ in range(n)], n == 1
        if len(set(outs)) != len(outs):
            raise OnnxwError(f"{what}: dezelfde uitvoer twee keer")
        for o in outs:
            if o in self._values:
                raise OnnxwError(f"{what}, uitvoer: de naam {o!r} bestaat al in de graaf")
        node_name = self._new(f"{op_type}_") if name is None else _name(name, f"{what}, naam")
        if node_name in self._node_names:
            raise OnnxwError(f"{what}: de nodenaam {node_name!r} bestaat al")
        attributes = tuple(_attribute(k, attrs[k], what) for k in sorted(attrs))
        # pas nu alles klopt: een fout laat de graaf zoals hij was
        self._node_names.add(node_name)
        self._values.update(outs)
        node = _Node(op_type, tuple(inputs), tuple(outs), node_name, domain, attributes)
        self._nodes.append(node)
        return outs[0] if single else outs

    def output(self, name: str, dtype: int, shape: Sequence[int | str | None] | None) -> str:
        what = f"uitvoer {name!r}"
        _type(dtype)
        if _name(name, "uitvoer") not in self._values:
            raise OnnxwError(f"{what}: bestaat niet in de graaf")
        if name in self._out_names:
            raise OnnxwError(f"{what}: staat al bij de uitvoer")
        self._out_names.add(name)
        self._outputs.append(_value_info(name, dtype, _shape(shape, what)))
        return name

    def _domains(self) -> set[str]:
        return {n.domain for n in self._nodes}

    def _encode(self) -> _Msg:
        """GraphProto: node 1, name 2, initializer 5, input 11, output 12."""
        g = _Msg()
        for n in self._nodes:
            node = _Msg()  # NodeProto: input 1, output 2, name 3, op_type 4, attribute 5, domain 7
            for i in n.inputs:
                node.text(1, i)
            for o in n.outputs:
                node.text(2, o)
            node.text(3, n.name)
            node.text(4, n.op_type)
            for a in n.attrs:
                node.msg(5, a)
            if n.domain:
                node.text(7, n.domain)
            g.msg(1, node)
        g.text(2, self.name)
        for t in self._inits:
            g.msg(5, _tensor(t))
        for info in self._inputs:
            g.msg(11, info)
        for info in self._outputs:
            g.msg(12, info)
        return g


class Model:
    """Een ModelProto rond een graaf: ir_version 1, producer_name 2, graph 7, opset_import 8.

    opsets geeft de versie per domein (standaard OPSETS: "" 17 en com.microsoft 1); "" staat altijd
    in het model, een ander domein alleen als een node het gebruikt. De vorm van de graaf wordt hier
    vastgelegd (wat er daarna bij komt, telt voor dit model niet); de gegevens van de tensors leest
    pas to_bytes of write."""

    def __init__(
        self,
        graph: Graph,
        *,
        opsets: dict[str, int] | None = None,
        ir_version: int = IR_VERSION,
        producer: str = PRODUCER,
    ) -> None:
        opsets = dict(OPSETS if opsets is None else opsets)
        if "" not in opsets:
            raise OnnxwError('opsets: het standaarddomein "" ontbreekt')
        if not graph._outputs:
            raise OnnxwError(f"graaf {graph.name!r}: geen uitvoer")
        used = graph._domains() | {""}
        for domain in sorted(used):
            if domain not in opsets:
                raise OnnxwError(f"domein {domain!r}: geen versie in opsets")
        m = _Msg()
        m.varint(1, _int(ir_version, "ir_version", 1))
        m.text(2, _name(producer, "producer"))
        m.msg(7, graph._encode())
        for domain in sorted(used):
            opset = _Msg()  # OperatorSetIdProto: domain 1, version 2
            opset.text(1, domain)
            opset.varint(2, _int(opsets[domain], f"opset {domain!r}", 1))
            m.msg(8, opset)
        if m.size > MAX_BYTES:
            raise OnnxwError(
                f"het model wordt {m.size} bytes; protobuf kan hooguit {MAX_BYTES} "
                "(groter vraagt externe gegevens)"
            )
        self._msg = m
        self.size = m.size

    def _chunks(self) -> Iterator[memoryview]:
        for part in self._msg.parts:
            yield memoryview(part) if isinstance(part, bytearray) else part.buffer()

    def to_bytes(self) -> bytes:
        """Het hele model als bytes: voor kleine modellen (in een test, een nepmodel)."""
        return b"".join(self._chunks())

    def write(self, target: str | os.PathLike[str] | BinaryIO) -> Written:
        """Het model in stukken naar een pad of een open binair bestand; de sha256 onderweg."""
        if not isinstance(target, (str, os.PathLike)):
            return self._stream(target)
        path = Path(target)
        part = path.with_name(path.name + ".deel")
        with open(part, "xb") as f:  # O_EXCL: nooit over een bestand of een symlink heen
            try:
                written = self._stream(f)
                f.flush()
                os.fsync(f.fileno())
            except BaseException:
                part.unlink(missing_ok=True)
                raise
        try:
            os.replace(part, path)
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        return written

    def _stream(self, f: BinaryIO) -> Written:
        digest = hashlib.sha256()
        size = 0
        for chunk in self._chunks():
            digest.update(chunk)
            size += chunk.nbytes
            while chunk:  # een ongebufferd bestand kan minder schrijven dan gevraagd
                n = f.write(chunk)
                if n is None or n >= chunk.nbytes:
                    break
                chunk = chunk[n:]
        if size != self.size:
            raise OnnxwError(f"{size} bytes geschreven, verwacht {self.size}")
        return Written(size, digest.hexdigest())

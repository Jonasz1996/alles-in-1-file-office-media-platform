"""De ONNX-schrijver met alleen de standaardbibliotheek (fase 6, X3a; app/analyse/onnxw.py).

Een eigen, strenge lezer van protobuf (ook alleen de standaardbibliotheek) leest terug wat onnxw
schreef en kijkt elk veld na: het nummer, het draadtype, de volgorde en de waarde, en of er niets
anders staat. Daarnaast: dezelfde invoer geeft dezelfde bytes, de fouten, en het schrijven in
stukken (zonder tweede kopie in het geheugen). Of onnxruntime de modellen laadt en goed rekent,
staat in tests_analyse/test_onnxw_ort.py (met numpy; job api-analyse).
"""

from __future__ import annotations

import array
import ctypes
import hashlib
import io
import os
import struct
import subprocess
import sys
import tracemalloc
from pathlib import Path

import pytest

from app.analyse import onnxw
from app.analyse.onnxw import FLOAT, FLOAT16, INT8, INT32, INT64, UINT8, Graph, Model, OnnxwError

API = Path(__file__).resolve().parents[2]
MiB = 1 << 20

# --- een strenge lezer van protobuf ----------------------------------------------------------

# bericht -> veldnummer -> (naam, soort, herhaald); soort: int, float, str, bytes of een bericht
SCHEMA: dict[str, dict[int, tuple[str, str, bool]]] = {
    "Model": {
        1: ("ir_version", "int", False),
        2: ("producer_name", "str", False),
        7: ("graph", "Graph", False),
        8: ("opset_import", "Opset", True),
    },
    "Opset": {1: ("domain", "str", False), 2: ("version", "int", False)},
    "Graph": {
        1: ("node", "Node", True),
        2: ("name", "str", False),
        5: ("initializer", "Tensor", True),
        11: ("input", "ValueInfo", True),
        12: ("output", "ValueInfo", True),
    },
    "Node": {
        1: ("input", "str", True),
        2: ("output", "str", True),
        3: ("name", "str", False),
        4: ("op_type", "str", False),
        5: ("attribute", "Attribute", True),
        7: ("domain", "str", False),
    },
    "Attribute": {
        1: ("name", "str", False),
        2: ("f", "float", False),
        3: ("i", "int", False),
        4: ("s", "bytes", False),
        5: ("t", "Tensor", False),
        7: ("floats", "float", True),
        8: ("ints", "int", True),
        20: ("type", "int", False),
    },
    "Tensor": {
        1: ("dims", "int", True),
        2: ("data_type", "int", False),
        8: ("name", "str", False),
        9: ("raw_data", "bytes", False),
    },
    "ValueInfo": {1: ("name", "str", False), 2: ("type", "Type", False)},
    "Type": {1: ("tensor_type", "TensorType", False)},
    "TensorType": {1: ("elem_type", "int", False), 2: ("shape", "Shape", False)},
    "Shape": {1: ("dim", "Dim", True)},
    "Dim": {1: ("dim_value", "int", False), 2: ("dim_param", "str", False)},
}
WIRE = {"int": 0, "float": 5}  # de rest: 2 (lengte ervoor)


def read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    n = shift = 0
    start = pos
    while True:
        assert pos < len(buf), "varint loopt over het einde"
        b = buf[pos]
        pos += 1
        n |= (b & 0x7F) << shift
        if not b & 0x80:
            break
        shift += 7
        assert shift < 70, "varint langer dan tien bytes"
    assert pos - start == 1 or buf[pos - 1] != 0, "varint niet zo kort mogelijk"
    return n, pos


def decode(buf: bytes, kind: str = "Model") -> dict:
    """Het bericht als dict; herhaalde velden als lijst. Een onbekend veld, een ander draadtype,
    een niet-herhaald veld twee keer of velden buiten de volgorde van hun nummer: AssertionError."""
    schema = SCHEMA[kind]
    out: dict = {}
    pos = last = 0
    while pos < len(buf):
        key, pos = read_varint(buf, pos)
        field, wire = key >> 3, key & 7
        assert field in schema, f"{kind}: onbekend veld {field}"
        assert field >= last, f"{kind}: veld {field} na veld {last}"
        last = field
        name, sort, repeated = schema[field]
        assert wire == WIRE.get(sort, 2), f"{kind}.{name}: draadtype {wire}"
        if wire == 0:
            value, pos = read_varint(buf, pos)
            value = value - (1 << 64) if value >= 1 << 63 else value  # int64
        elif wire == 5:
            assert pos + 4 <= len(buf)
            (value,) = struct.unpack_from("<f", buf, pos)
            pos += 4
        else:
            n, pos = read_varint(buf, pos)
            assert pos + n <= len(buf), f"{kind}.{name}: lengte voorbij het einde"
            raw = bytes(buf[pos : pos + n])
            pos += n
            if sort == "str":
                value = raw.decode("utf-8")
            elif sort == "bytes":
                value = raw
            else:
                value = decode(raw, sort)
        if repeated:
            out.setdefault(name, []).append(value)
        else:
            assert name not in out, f"{kind}.{name}: twee keer"
            out[name] = value
    assert pos == len(buf)
    return out


def info(name: str, elem: int, shape: list | None) -> dict:
    """Wat decode van een ValueInfoProto maakt."""
    tensor_type: dict = {"elem_type": elem}
    if shape is not None:
        dims = [
            {} if d is None else {"dim_param": d} if isinstance(d, str) else {"dim_value": d}
            for d in shape
        ]
        tensor_type["shape"] = {"dim": dims} if dims else {}
    return {"name": name, "type": {"tensor_type": tensor_type}}


# --- een model met elk veld --------------------------------------------------------------------


def alles() -> Graph:
    """Elk type, elke soort buffer en attribuut, symbolische en onbekende assen, twee uitvoeren,
    een node van com.microsoft. De bytes betekenen niets; de vorm telt."""
    g = Graph("alles")
    g.input("beeld", FLOAT, [1, 3, "height", "width"])
    g.input("ids", INT64, [1, None])
    g.input("vrij", FLOAT, None)
    g.initializer("f32", FLOAT, [2], onnxw.pack(FLOAT, [1.5, -2.0]))
    g.initializer("f16", FLOAT16, [2], bytearray(onnxw.pack(FLOAT16, [0.5, -1.0])))
    g.initializer("i8", INT8, [3], array.array("b", [-128, 0, 127]))
    g.initializer("u8", UINT8, [1, 2], memoryview(bytes([0, 255])))
    g.initializer("i32", INT32, [], array.array("i", [-7]))
    g.initializer("i64", INT64, [2], lambda: array.array("q", [-1, 2**40]))
    g.initializer("leeg", FLOAT, [0], b"")
    c = g.initializer(None, FLOAT, [1], onnxw.pack(FLOAT, [0.25]))
    y = g.op(
        "Conv",
        ["beeld", "f32", ""],
        strides=[2, 2],
        pads=[1, 1, 1, 1],
        kernel_shape=[3, 3],
        group=1,
    )
    y = g.op("LayerNormalization", [y, c], epsilon=1e-6, axis=-1)
    a, b = g.op("Split", [y], 2, axis=-2)
    g.op("Concat", [a, b], outputs="samen", axis=0, name="eigen naam")
    g.op(
        "Constant",
        [],
        outputs=["k"],
        value=onnxw.Tensor("", INT64, [2], onnxw.pack(INT64, [3, -3])),
    )
    g.op("Resize", ["samen"], mode="nearest", scales=[1.0, 2.5], coordinate=b"\x00\xff")
    g.op(
        "DynamicQuantizeMatMul",
        ["vrij", "i8", "f32", "i8", ""],
        outputs="q",
        domain="com.microsoft",
    )
    g.output("samen", FLOAT, [1, 3, "h2", "w2"])
    g.output("q", FLOAT, [])
    return g


def tensor(name: str, dtype: int, dims: list[int], raw: bytes) -> dict:
    out: dict = {"dims": dims} if dims else {}
    out["data_type"] = dtype
    if name:
        out["name"] = name
    out["raw_data"] = raw
    return out


VERWACHT = {
    "ir_version": 8,
    "producer_name": "platform-onnxw",
    "graph": {
        "node": [
            {
                "input": ["beeld", "f32", ""],
                "output": ["t2"],
                "name": "Conv_3",
                "op_type": "Conv",
                "attribute": [  # op naam
                    {"name": "group", "i": 1, "type": 2},
                    {"name": "kernel_shape", "ints": [3, 3], "type": 7},
                    {"name": "pads", "ints": [1, 1, 1, 1], "type": 7},
                    {"name": "strides", "ints": [2, 2], "type": 7},
                ],
            },
            {
                "input": ["t2", "c1"],
                "output": ["t4"],
                "name": "LayerNormalization_5",
                "op_type": "LayerNormalization",
                "attribute": [
                    {"name": "axis", "i": -1, "type": 2},
                    {
                        "name": "epsilon",
                        "f": struct.unpack("<f", struct.pack("<f", 1e-6))[0],
                        "type": 1,
                    },
                ],
            },
            {
                "input": ["t4"],
                "output": ["t6", "t7"],
                "name": "Split_8",
                "op_type": "Split",
                "attribute": [{"name": "axis", "i": -2, "type": 2}],
            },
            {
                "input": ["t6", "t7"],
                "output": ["samen"],
                "name": "eigen naam",
                "op_type": "Concat",
                "attribute": [{"name": "axis", "i": 0, "type": 2}],
            },
            {
                "output": ["k"],
                "name": "Constant_9",
                "op_type": "Constant",
                "attribute": [
                    {
                        "name": "value",
                        "t": tensor("", INT64, [2], struct.pack("<2q", 3, -3)),
                        "type": 4,
                    }
                ],
            },
            {
                "input": ["samen"],
                "output": ["t10"],
                "name": "Resize_11",
                "op_type": "Resize",
                "attribute": [
                    {"name": "coordinate", "s": b"\x00\xff", "type": 3},
                    {"name": "mode", "s": b"nearest", "type": 3},
                    {"name": "scales", "floats": [1.0, 2.5], "type": 6},
                ],
            },
            {
                "input": ["vrij", "i8", "f32", "i8", ""],
                "output": ["q"],
                "name": "DynamicQuantizeMatMul_12",
                "op_type": "DynamicQuantizeMatMul",
                "domain": "com.microsoft",
            },
        ],
        "name": "alles",
        "initializer": [
            tensor("f32", FLOAT, [2], struct.pack("<2f", 1.5, -2.0)),
            tensor("f16", FLOAT16, [2], struct.pack("<2e", 0.5, -1.0)),
            tensor("i8", INT8, [3], struct.pack("<3b", -128, 0, 127)),
            tensor("u8", UINT8, [1, 2], b"\x00\xff"),
            tensor("i32", INT32, [], struct.pack("<i", -7)),
            tensor("i64", INT64, [2], struct.pack("<2q", -1, 2**40)),
            tensor("leeg", FLOAT, [0], b""),
            tensor("c1", FLOAT, [1], struct.pack("<f", 0.25)),
        ],
        "input": [
            info("beeld", FLOAT, [1, 3, "height", "width"]),
            info("ids", INT64, [1, None]),
            info("vrij", FLOAT, None),
        ],
        "output": [info("samen", FLOAT, [1, 3, "h2", "w2"]), info("q", FLOAT, [])],
    },
    "opset_import": [
        {"domain": "", "version": 17},
        {"domain": "com.microsoft", "version": 1},
    ],
}
# De sha256 van Model(alles()). Verandert hij, dan veranderen ook de omgezette modellen van X3 en
# hun regels in models.lock: alleen met opzet (Keuze 73)
SHA_ALLES = "623e3aaea109d25186d33ef63b8a3d08108bb1961bc4b958c5b6db8014b6e7c0"


def test_elk_veld_teruggelezen():
    assert decode(Model(alles()).to_bytes()) == VERWACHT


def test_dezelfde_invoer_dezelfde_bytes():
    een, twee = Model(alles()).to_bytes(), Model(alles()).to_bytes()
    assert een == twee
    assert hashlib.sha256(een).hexdigest() == SHA_ALLES


def test_de_volgorde_van_de_attributen_telt_niet():
    def bouw(volgorde: list[str]) -> bytes:
        g = Graph("g")
        g.input("x", FLOAT, [1, 4])
        attrs = {"axes": [1], "keepdims": 0, "noop_with_empty_axes": 0}
        g.op("ReduceMean", ["x"], outputs="y", **{k: attrs[k] for k in volgorde})
        g.output("y", FLOAT, [1])
        return Model(g).to_bytes()

    assert bouw(["axes", "keepdims", "noop_with_empty_axes"]) == bouw(
        ["noop_with_empty_axes", "keepdims", "axes"]
    )


@pytest.mark.parametrize(
    ("n", "draad"),
    [
        (0, b"\x00"),
        (1, b"\x01"),
        (127, b"\x7f"),
        (128, b"\x80\x01"),
        (300, b"\xac\x02"),
        (2**63 - 1, b"\xff" * 8 + b"\x7f"),
        (-1, b"\xff" * 9 + b"\x01"),
        (-(2**63), b"\x80" * 9 + b"\x01"),
    ],
)
def test_varint_ook_negatief_als_twee_complement(n, draad):
    assert onnxw._varint(n) == draad
    value, pos = read_varint(draad, 0)
    assert pos == len(draad)
    assert (value - (1 << 64) if value >= 1 << 63 else value) == n


def test_alleen_gebruikte_domeinen_en_eigen_versies():
    g = Graph("g")
    g.input("x", FLOAT, [1])
    g.op("Relu", ["x"], outputs="y")
    g.output("y", FLOAT, [1])
    assert decode(Model(g).to_bytes())["opset_import"] == [{"domain": "", "version": 17}]
    model = Model(g, opsets={"": 13}, ir_version=7, producer="ander")
    m = decode(model.to_bytes())
    assert (m["ir_version"], m["producer_name"], m["opset_import"]) == (
        7,
        "ander",
        [{"domain": "", "version": 13}],
    )


def test_een_eigen_domein_met_versie():
    g = Graph("g")
    g.input("x", FLOAT, [1])
    g.op("Eigen", ["x"], outputs="y", domain="nl.voorbeeld")
    g.output("y", FLOAT, [1])
    with pytest.raises(OnnxwError, match="domein 'nl.voorbeeld': geen versie in opsets"):
        Model(g)
    m = decode(Model(g, opsets={"": 17, "nl.voorbeeld": 3}).to_bytes())
    assert m["opset_import"] == [
        {"domain": "", "version": 17},
        {"domain": "nl.voorbeeld", "version": 3},
    ]


def test_een_graaf_na_het_model_veranderen_telt_niet():
    g = Graph("g")
    g.input("x", FLOAT, [1])
    g.op("Relu", ["x"], outputs="y")
    g.output("y", FLOAT, [1])
    model = Model(g)
    voor = model.to_bytes()
    g.op("Relu", ["y"], outputs="z")
    g.output("z", FLOAT, [1])
    assert model.to_bytes() == voor
    assert len(decode(Model(g).to_bytes())["graph"]["node"]) == 2


def test_pack():
    assert onnxw.pack(FLOAT, [1.5, -2]) == struct.pack("<2f", 1.5, -2.0)
    assert onnxw.pack(FLOAT16, (x for x in [0.5])) == struct.pack("<e", 0.5)
    assert onnxw.pack(INT64, [-1]) == b"\xff" * 8
    assert onnxw.pack(UINT8, []) == b""
    with pytest.raises(OnnxwError, match="pack als int8"):
        onnxw.pack(INT8, [128])
    with pytest.raises(OnnxwError, match="pack als float32"):
        onnxw.pack(FLOAT, [1e40])
    with pytest.raises(OnnxwError, match="onbekend type 11"):
        onnxw.pack(11, [1.0])


# --- fouten -----------------------------------------------------------------------------------


def _g() -> Graph:
    g = Graph("g")
    g.input("x", FLOAT, [1, 4])
    return g


class Strided:
    """Een buffer die niet aaneengesloten is."""


FOUTEN = [
    # (wat, aanroep op een verse graaf, stuk van de melding)
    ("onbekend type", lambda g: g.initializer("w", 11, [1], b"\0" * 8), "onbekend type 11"),
    ("type als bool", lambda g: g.input("y", True, [1]), "onbekend type True"),
    ("type als tekst", lambda g: g.input("y", "float", [1]), "onbekend type 'float'"),
    (
        "vorm past niet bij de bytes",
        lambda g: g.initializer("w", FLOAT, [2, 3], b"\0" * 20),
        "tensor 'w': 20 bytes, maar de vorm als float32 vraagt 24",
    ),
    (
        "float64 als FLOAT",
        lambda g: g.initializer("w", FLOAT, [2], array.array("d", [1.0])),
        "tensor 'w': een buffer met formaat 'd' past niet bij float32",
    ),
    (
        "int8 als UINT8",
        lambda g: g.initializer("w", UINT8, [2], array.array("b", [1, 2])),
        "formaat 'b' past niet bij uint8",
    ),
    (
        "int32 als INT64",
        lambda g: g.initializer("w", INT64, [2], array.array("i", [1, 2, 3, 4])),
        "formaat 'i' past niet bij int64",
    ),
    (
        "big-endian",
        lambda g: g.initializer("w", FLOAT, [2], (ctypes.c_float.__ctype_be__ * 2)(1, 2)),
        "tensor 'w': de buffer is big-endian ('>f'), ONNX is little-endian",
    ),
    (
        "niet aaneengesloten",
        lambda g: g.initializer("w", UINT8, [2], memoryview(b"abcd")[::2]),
        "tensor 'w': de buffer is niet aaneengesloten",
    ),
    ("tekst als gegevens", lambda g: g.initializer("w", UINT8, [1], "a"), "zijn tekst, geen bytes"),
    (
        "lijst als gegevens",
        lambda g: g.initializer("w", FLOAT, [1], [1.0]),
        "tensor 'w': list is geen buffer",
    ),
    ("negatieve as", lambda g: g.initializer("w", FLOAT, [-1], b""), "as 0: -1 ligt buiten"),
    ("as als float", lambda g: g.initializer("w", FLOAT, [1.0], b"\0" * 4), "1.0 is geen geheel"),
    ("vorm als int", lambda g: g.initializer("w", FLOAT, 4, b"\0" * 16), "is geen lijst"),
    ("lege naam", lambda g: g.initializer("", FLOAT, [1], b"\0" * 4), "is geen naam"),
    ("naam bestaat", lambda g: g.initializer("x", FLOAT, [1], b"\0" * 4), "'x' bestaat al"),
    ("invoer twee keer", lambda g: g.input("x", FLOAT, [1]), "'x' bestaat al"),
    ("lege symbolische as", lambda g: g.input("y", FLOAT, [""]), "as 0: een symbolische as"),
    ("vorm als tekst", lambda g: g.input("y", FLOAT, "1x4"), "geen lijst, tuple of None"),
    ("onbekende invoer", lambda g: g.op("Relu", ["nee"]), "invoer 'nee' bestaat (nog) niet"),
    ("invoer als tekst", lambda g: g.op("Relu", "x"), "inputs is geen lijst"),
    ("uitvoer bestaat", lambda g: g.op("Relu", ["x"], outputs="x"), "'x' bestaat al"),
    ("uitvoer dubbel", lambda g: g.op("Split", ["x"], outputs=["a", "a"]), "twee keer"),
    ("geen uitvoer", lambda g: g.op("Relu", ["x"], outputs=[]), "geen uitvoer"),
    ("nul uitvoeren", lambda g: g.op("Relu", ["x"], outputs=0), "0 ligt buiten 1..1000"),
    ("lege op", lambda g: g.op("", ["x"]), "op: '' is geen naam"),
    ("domein geen str", lambda g: g.op("Relu", ["x"], domain=None), "domein None is geen str"),
    (
        "nodenaam dubbel",
        lambda g: (g.op("Relu", ["x"], name="n"), g.op("Relu", ["x"], name="n")),
        "nodenaam 'n' bestaat al",
    ),
    ("lege lijst", lambda g: g.op("Relu", ["x"], axes=[]), "'axes': een lege lijst"),
    ("gemengde lijst", lambda g: g.op("Relu", ["x"], axes=[1, "a"]), "'axes': een lijst van"),
    ("attribuut dict", lambda g: g.op("Relu", ["x"], a={}), "'a': dict kan niet"),
    ("attribuut None", lambda g: g.op("Relu", ["x"], a=None), "'a': NoneType kan niet"),
    ("float te groot", lambda g: g.op("Relu", ["x"], a=1e40), "'a': 1e+40 past niet in float32"),
    ("floats te groot", lambda g: g.op("Relu", ["x"], a=[1.0, 1e40]), "past niet in float32"),
    ("int te groot", lambda g: g.op("Relu", ["x"], a=2**63), "ligt buiten"),
    ("ints te klein", lambda g: g.op("Relu", ["x"], a=[-(2**63) - 1]), "ligt buiten"),
    ("uitvoer bestaat niet", lambda g: g.output("y", FLOAT, [1]), "uitvoer 'y': bestaat niet"),
    (
        "uitvoer twee keer",
        lambda g: (g.output("x", FLOAT, [1]), g.output("x", FLOAT, [1])),
        "staat al bij de uitvoer",
    ),
]


@pytest.mark.parametrize(
    ("aanroep", "melding"), [f[1:] for f in FOUTEN], ids=[f[0] for f in FOUTEN]
)
def test_een_heldere_fout(aanroep, melding):
    g = _g()
    with pytest.raises(OnnxwError) as e:
        aanroep(g)
    assert melding in str(e.value)


def test_buffers_met_een_eigen_formaat():
    """Een formaat met expliciete volgorde, zoals ctypes (en numpy) dat geeft: '<' telt als
    little-endian; een geheel type van 8 bytes mag 'q' of 'l' heten, van 4 bytes 'i' of 'l'."""
    g = _g()
    g.initializer("f", FLOAT, [2], (ctypes.c_float.__ctype_le__ * 2)(1.5, 2))
    g.initializer("i", INT32, [1], (ctypes.c_int32.__ctype_le__ * 1)(-5))
    g.initializer("q", INT64, [1], (ctypes.c_int64.__ctype_le__ * 1)(7))
    g.initializer(
        "l",
        INT64,
        [1],
        array.array("l", [8]) if array.array("l").itemsize == 8 else array.array("q", [8]),
    )
    g.initializer("e", FLOAT16, [2, 1], memoryview(struct.pack("<2e", 1, 2)).cast("e", (2, 1)))
    g.output("x", FLOAT, [1, 4])
    raw = {t["name"]: t["raw_data"] for t in decode(Model(g).to_bytes())["graph"]["initializer"]}
    assert raw == {
        "f": struct.pack("<2f", 1.5, 2),
        "i": struct.pack("<i", -5),
        "q": struct.pack("<q", 7),
        "l": struct.pack("<q", 8),
        "e": struct.pack("<2e", 1, 2),
    }


def test_een_model_zonder_uitvoer_of_standaarddomein():
    with pytest.raises(OnnxwError, match="graaf 'g': geen uitvoer"):
        Model(_g())
    g = _g()
    g.output("x", FLOAT, [1, 4])
    with pytest.raises(OnnxwError, match='het standaarddomein "" ontbreekt'):
        Model(g, opsets={"com.microsoft": 1})


def test_een_callable_met_de_verkeerde_grootte_faalt_bij_het_schrijven(tmp_path):
    g = _g()
    g.initializer("w", FLOAT, [4], lambda: b"\0" * 12)
    g.output("x", FLOAT, [1, 4])
    model = Model(g)  # de grootte volgt uit de vorm: nog niets gelezen
    doel = tmp_path / "m.onnx"
    doel.write_bytes(b"oud")
    with pytest.raises(OnnxwError, match=r"tensor 'w' \(callable\): 12 bytes, maar"):
        model.write(doel)
    assert doel.read_bytes() == b"oud"  # het oude bestand blijft heel
    assert sorted(p.name for p in tmp_path.iterdir()) == ["m.onnx"]
    with pytest.raises(OnnxwError, match="callable"):
        model.to_bytes()


def test_groter_dan_2_gib_kan_niet():
    g = _g()
    g.initializer("w", UINT8, [2**31], lambda: pytest.fail("niet gelezen"))
    g.output("x", FLOAT, [1, 4])
    with pytest.raises(OnnxwError, match="protobuf kan hooguit 2147483647"):
        Model(g)


# --- schrijven in stukken ---------------------------------------------------------------------


def test_schrijven_naar_een_pad_en_een_bestand(tmp_path):
    model = Model(alles())
    verwacht = model.to_bytes()
    doel = tmp_path / "m.onnx"
    written = model.write(doel)
    assert written == onnxw.Written(len(verwacht), hashlib.sha256(verwacht).hexdigest())
    assert written.size == model.size
    assert doel.read_bytes() == verwacht
    assert sorted(p.name for p in tmp_path.iterdir()) == ["m.onnx"]  # geen .deel over
    buf = io.BytesIO()
    assert model.write(buf) == written
    assert buf.getvalue() == verwacht
    # nog een keer naar hetzelfde pad: vervangen, met dezelfde bytes
    assert model.write(str(doel)) == written


class Druppel(io.RawIOBase):
    """Een ongebufferd bestand dat per write hooguit 7 bytes neemt."""

    def __init__(self) -> None:
        self.data = bytearray()

    def writable(self) -> bool:
        return True

    def write(self, b) -> int:
        n = min(7, len(b))
        self.data += bytes(b[:n])
        return n


def test_een_bestand_dat_minder_schrijft_dan_gevraagd():
    model = Model(alles())
    druppel = Druppel()
    written = model.write(druppel)
    assert bytes(druppel.data) == model.to_bytes()
    assert written.sha256 == SHA_ALLES


def test_een_fout_laat_de_graaf_zoals_hij_was():
    g = _g()
    with pytest.raises(OnnxwError, match="'x' bestaat al"):
        g.op("Split", ["x"], outputs=["a", "x"])
    with pytest.raises(OnnxwError, match="'a': dict kan niet"):
        g.op("Relu", ["x"], outputs="b", name="n", a={})
    g.op("Relu", ["x"], outputs="a", name="n")  # a en n zijn nog vrij
    g.op("Relu", ["a"], outputs="b")
    g.output("b", FLOAT, [1, 4])
    nodes = decode(Model(g).to_bytes())["graph"]["node"]
    assert [(n["name"], n["output"]) for n in nodes] == [("n", ["a"]), ("Relu_1", ["b"])]


def test_nooit_over_een_bestaand_deel_of_een_symlink(tmp_path):
    model = Model(alles())
    deel = tmp_path / "m.onnx.deel"
    deel.write_bytes(b"van iemand anders")
    with pytest.raises(FileExistsError):
        model.write(tmp_path / "m.onnx")
    assert deel.read_bytes() == b"van iemand anders"
    deel.unlink()
    doelwit = tmp_path / "doelwit"
    doelwit.write_bytes(b"heel")
    deel.symlink_to(doelwit)
    with pytest.raises(FileExistsError):
        model.write(tmp_path / "m.onnx")
    assert doelwit.read_bytes() == b"heel"
    assert not (tmp_path / "m.onnx").exists()


def _groot(lazy: int, size: int, eager: bytearray | None = None) -> tuple[Model, list[int]]:
    """Een model met `lazy` callables van `size` bytes (elk een nieuwe bytearray), en eventueel
    één gewone buffer; calls telt de aanroepen per callable."""
    calls = [0] * lazy
    g = Graph("groot")
    g.input("x", FLOAT, [1])
    for i in range(lazy):

        def maak(i: int = i) -> bytearray:
            calls[i] += 1
            return bytearray(size)

        g.initializer(f"w{i}", UINT8, [size], maak)
    if eager is not None:
        g.initializer("eager", UINT8, [len(eager)], eager)
    g.output("x", FLOAT, [1])
    return Model(g), calls


def _piek(werk) -> int:
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        werk()
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def test_een_grote_buffer_wordt_niet_gekopieerd(tmp_path):
    model, _ = _groot(0, 0, bytearray(16 * MiB))
    piek = _piek(lambda: model.write(tmp_path / "m.onnx"))
    assert piek < MiB, piek
    assert (tmp_path / "m.onnx").stat().st_size == model.size > 16 * MiB


def test_callables_een_voor_een(tmp_path):
    model, calls = _groot(4, 4 * MiB)
    piek = _piek(lambda: model.write(tmp_path / "m.onnx"))
    assert calls == [1, 1, 1, 1]
    # één buffer tegelijk: 4 MiB en wat kleins, niet 8 of 16 MiB
    assert 4 * MiB <= piek < 5 * MiB, piek
    assert (tmp_path / "m.onnx").stat().st_size == model.size
    model.write(io.BytesIO())
    assert calls == [2, 2, 2, 2]


def test_alleen_de_standaardbibliotheek():
    """onnxw laadt niets buiten de standaardbibliotheek (de omzetter en de nepmodellen van X2a
    bouwen er zonder numpy mee; -S: ook geen site-packages)."""
    code = (
        f"import sys; sys.path.insert(0, {str(API)!r})\n"
        "import app.analyse.onnxw\n"
        "print(sorted(m for m in sys.modules if m.split('.')[0] not in sys.stdlib_module_names))"
    )
    out = subprocess.run(
        [sys.executable, "-I", "-S", "-c", code], capture_output=True, text=True, timeout=60
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.split("\n")[0] == "['__main__', 'app', 'app.analyse', 'app.analyse.onnxw']"
    assert os.path.basename(onnxw.__file__) == "onnxw.py"
